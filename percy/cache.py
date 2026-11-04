import threading
import time

class Cache:
    CACHE  = {}
    CACHE_TIMEOUT = 5 * 60 # 300 seconds
    TIMEOUT_KEY = 'last_access_time'
    # Guards CACHE so parallel runners sharing a process can't mutate it mid-iteration
    LOCK = threading.RLock()

    # Caching Keys
    capabilities = 'capabilities'
    command_executor_url = 'command_executor_url'

    @classmethod
    def check_types(cls, session_id, property):
        if not isinstance(session_id, str):
            raise TypeError('Argument session_id should be string')
        if not isinstance(property, str):
            raise TypeError('Argument property should be string')

    @classmethod
    def set_cache(cls, session_id, property, value):
        cls.check_types(session_id, property)
        with cls.LOCK:
            session = cls.CACHE.get(session_id, {})
            session[cls.TIMEOUT_KEY] = time.time()
            session[property] = value
            cls.CACHE[session_id] = session

    @classmethod
    def get_cache(cls, session_id, property):
        cls.cleanup_cache()
        cls.check_types(session_id, property)
        with cls.LOCK:
            session = cls.CACHE.get(session_id, {})
            return session.get(property, None)

    @classmethod
    def cleanup_cache(cls):
        now = time.time()
        with cls.LOCK:
            session_ids = [
                session_id for session_id, session in cls.CACHE.items()
                if now - session[cls.TIMEOUT_KEY] >= cls.CACHE_TIMEOUT
            ]
            for session_id in session_ids:
                cls.CACHE.pop(session_id, None)
