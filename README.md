# Herramientas de desarrollo

```
python -m venv venv
venv\Scripts\Activate
pip install fastapi uvicorn

-- Levanta el servidor
uvicorn main:app --reload

--Encriptador
pip install "python-jose[cryptography]"
pip install bcrypt


--Realizar pruebas manuales
!el servidor debe estar corriendo con pip install fastapi uvicorn¡
http://localhost:8000/docs

-- Realizar pruebas automatizadas
pip install pytest httpx
pytest -v
```
