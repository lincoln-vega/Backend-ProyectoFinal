ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS auth_user_id UUID
        REFERENCES auth.users(id) ON DELETE CASCADE,
    DROP COLUMN IF EXISTS password,
    DROP COLUMN IF EXISTS universidad;

CREATE UNIQUE INDEX IF NOT EXISTS users_auth_user_id_uidx
    ON public.users (auth_user_id);
