import os

# Keep tests from creating data/ugen.db: routes resolve the usage store from settings.
os.environ["USAGE_DB_PATH"] = ":memory:"
