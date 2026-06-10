import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-minimum-32-chars!")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("RESEND_API_KEY", "re_test_key")
os.environ.setdefault("FROM_EMAIL", "test@receptai.com")
