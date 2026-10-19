import base64
import json
import os
from typing import Any

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from google.auth.transport.requests import AuthorizedSession
from pydantic import BaseModel

from auth import require_role
from database import load_db, save_db
from routers.google_oauth import credentials_for_user

router = APIRouter(prefix="/meet", tags=["Google Meet"])
MEET_SPACES_URL = "https://meet.googleapis.com/v2/spaces"
WORKSPACE_SUBSCRIPTIONS_URL = "https://workspaceevents.googleapis.com/v1/subscriptions"


class MeetSpaceResponse(BaseModel):
    cursoId: str
    meetingSpace: str
    meetUrl: str
    googleOrganizerEmail: str


class SubscriptionResponse(BaseModel):
    cursoId: str
    subscriptionName: str
    meetingSpace: str
    estado: str


def find_course(db: dict[str, Any], course_id: str) -> dict[str, Any]:
    course = next(
        (item for item in db.get("courses", []) if item.get("id") == course_id),
        None,
    )
    if course is None:
        raise HTTPException(status_code=404, detail="Curso no encontrado")
    return course


def ensure_course_owner(course: dict[str, Any], user: dict[str, Any]) -> None:
    if course.get("docenteId") != user.get("id"):
        raise HTTPException(
            status_code=403,
            detail="Solo el docente asignado puede crear el Meet del curso",
        )


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(
            status_code=503,
            detail=f"La configuración {name} es necesaria para recibir eventos de Meet",
        )
    return value


def find_course_by_space(db: dict[str, Any], meeting_space: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in db.get("courses", [])
            if item.get("meetSpaceName") == meeting_space
        ),
        None,
    )


@router.post(
    "/cursos/{course_id}/espacio",
    response_model=MeetSpaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_meet_space(
    course_id: str,
    current_user: dict = Depends(require_role(["docente"])),
):
    db = load_db()
    course = find_course(db, course_id)
    ensure_course_owner(course, current_user)

    if course.get("meetSpaceName") and course.get("meetUrl"):
        return MeetSpaceResponse(
            cursoId=course_id,
            meetingSpace=course["meetSpaceName"],
            meetUrl=course["meetUrl"],
            googleOrganizerEmail=course.get(
                "googleOrganizerEmail", current_user.get("googleEmail", "")
            ),
        )

    if not current_user.get("googleAccountVerified"):
        raise HTTPException(
            status_code=409,
            detail="El docente debe vincular primero su cuenta Google",
        )

    credentials = credentials_for_user(current_user)
    session = AuthorizedSession(credentials)
    response = session.post(MEET_SPACES_URL, json={})
    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail="Google Meet rechazó la creación del espacio",
        )

    payload = response.json()
    meeting_space = payload.get("name")
    meeting_uri = payload.get("meetingUri")
    if not meeting_space or not meeting_uri:
        raise HTTPException(
            status_code=502,
            detail="Google Meet no devolvió los datos del espacio creado",
        )

    course["meetSpaceName"] = meeting_space
    course["meetUrl"] = meeting_uri
    course["googleOrganizerEmail"] = current_user["googleEmail"]
    db["courses"] = db.get("courses", [])
    save_db(db)

    return MeetSpaceResponse(
        cursoId=course_id,
        meetingSpace=meeting_space,
        meetUrl=meeting_uri,
        googleOrganizerEmail=current_user["googleEmail"],
    )


@router.post(
    "/cursos/{course_id}/suscripcion",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_meet_subscription(
    course_id: str,
    current_user: dict = Depends(require_role(["docente"])),
):
    db = load_db()
    course = find_course(db, course_id)
    ensure_course_owner(course, current_user)
    meeting_space = course.get("meetSpaceName")
    if not meeting_space:
        raise HTTPException(
            status_code=409,
            detail="Primero debes crear el espacio de Meet del curso",
        )

    existing = next(
        (
            item
            for item in db.get("meetSubscriptions", [])
            if item.get("meetingSpace") == meeting_space
            and item.get("estado") == "ACTIVE"
        ),
        None,
    )
    if existing:
        return SubscriptionResponse(
            cursoId=course_id,
            subscriptionName=existing["subscriptionName"],
            meetingSpace=meeting_space,
            estado=existing["estado"],
        )

    project_id = required_env("GOOGLE_CLOUD_PROJECT_ID")
    topic_name = required_env("GOOGLE_PUBSUB_TOPIC")
    if not topic_name.startswith("projects/"):
        topic_name = f"projects/{project_id}/topics/{topic_name}"

    response = AuthorizedSession(credentials_for_user(current_user)).post(
        WORKSPACE_SUBSCRIPTIONS_URL,
        json={
            "targetResource": f"//meet.googleapis.com/{meeting_space}",
            "eventTypes": [
                "google.workspace.meet.participant.v2.joined",
                "google.workspace.meet.participant.v2.left",
            ],
            "notificationEndpoint": {"pubsubTopic": topic_name},
            "ttl": "86400s",
        },
    )
    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail="Google Workspace Events rechazó la suscripción",
        )

    payload = response.json()
    subscription_name = payload.get("name")
    if not subscription_name:
        raise HTTPException(
            status_code=502,
            detail="Google Workspace Events no devolvió el nombre de la suscripción",
        )

    subscription = {
        "subscriptionName": subscription_name,
        "cursoId": course_id,
        "meetingSpace": meeting_space,
        "estado": payload.get("state", "ACTIVE"),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    db.setdefault("meetSubscriptions", []).append(subscription)
    save_db(db)
    return SubscriptionResponse(
        cursoId=course_id,
        subscriptionName=subscription_name,
        meetingSpace=meeting_space,
        estado=subscription["estado"],
    )


@router.post("/events/pubsub", status_code=status.HTTP_204_NO_CONTENT)
def receive_meet_event(
    envelope: dict[str, Any],
    verification_token: str | None = Query(default=None, alias="token"),
):
    configured_token = os.getenv("GOOGLE_PUBSUB_VERIFICATION_TOKEN")
    if configured_token and verification_token != configured_token:
        raise HTTPException(status_code=401, detail="Token de Pub/Sub inválido")

    message = envelope.get("message")
    if not isinstance(message, dict):
        raise HTTPException(status_code=400, detail="Mensaje Pub/Sub inválido")

    message_id = str(message.get("messageId") or message.get("message_id") or "")
    attributes = message.get("attributes") or {}
    encoded_data = message.get("data")
    event_data: dict[str, Any] = {}
    if encoded_data:
        try:
            event_data = json.loads(
                base64.b64decode(encoded_data).decode("utf-8")
            )
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Datos del evento inválidos") from exc

    db = load_db()
    processed = db.setdefault("meetProcessedEvents", [])
    if message_id and any(item.get("messageId") == message_id for item in processed):
        return

    event = {
        "messageId": message_id,
        "eventType": attributes.get("ce-type") or attributes.get("eventType"),
        "resource": event_data.get("name") or event_data.get("resource"),
        "receivedAt": datetime.now(timezone.utc).isoformat(),
        "data": event_data,
    }
    processed.append(event)
    db["meetProcessedEvents"] = processed

    meeting_space = event_data.get("meetingSpace") or event_data.get("space")
    course = find_course_by_space(db, meeting_space) if meeting_space else None
    if course:
        session = event_data.get("participantSession") or event_data.get("session")
        participant_email = event_data.get("participantEmail")
        if session and participant_email == course.get("googleOrganizerEmail"):
            audit = {
                "courseId": course["id"],
                "docenteId": course.get("docenteId"),
                "meetingSpace": meeting_space,
                "participantSession": session,
                "eventType": event["eventType"],
                "participantEmail": participant_email,
                "receivedAt": event["receivedAt"],
            }
            db.setdefault("meetAttendanceAudit", []).append(audit)
    save_db(db)


@router.get("/cursos/{course_id}/auditoria")
def list_teacher_audit(
    course_id: str,
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    db = load_db()
    course = find_course(db, course_id)
    if current_user.get("rol") == "docente":
        ensure_course_owner(course, current_user)
    return [
        item
        for item in db.get("meetAttendanceAudit", [])
        if item.get("courseId") == course_id
    ]
