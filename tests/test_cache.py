# . # pylint: disable=[arguments-differ, protected-access]
import threading
import time
import unittest
from unittest.mock import patch
from percy.cache import Cache

class TestCache(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = Cache
        self.session_id = 'session_id_123'
        self.other_property = 'other_property'
        self.other_value = 'other_value'
        self.capabilities = {
            'browser': 'chrome',
            'platform': 'windows',
            'browserVersion': '115.0.1'
          }

        self.cache.set_cache(self.session_id, self.other_property, self.other_value)
        self.cache.set_cache(self.session_id, Cache.capabilities, self.capabilities)

    def test_set_cache(self):
        with self.assertRaises(Exception) as e:
            self.cache.set_cache(123, 123, 123)
        self.assertEqual(str(e.exception), 'Argument session_id should be string')

        with self.assertRaises(Exception) as e:
            self.cache.set_cache(self.session_id, 123, 123)
        self.assertEqual(str(e.exception), 'Argument property should be string')

        self.assertIn(self.session_id, self.cache.CACHE)
        self.assertEqual(self.cache.CACHE[self.session_id][self.other_property],
                         self.other_value)
        self.assertDictEqual(self.cache.CACHE[self.session_id][Cache.capabilities],
                             self.capabilities)

    def test_get_cache_invalid_args(self):
        with self.assertRaises(Exception) as e:
            self.cache.get_cache(123, 123)
        self.assertEqual(str(e.exception), 'Argument session_id should be string')

        with self.assertRaises(Exception) as e:
            self.cache.get_cache(self.session_id, 123)
        self.assertEqual(str(e.exception), 'Argument property should be string')

    @patch.object(Cache, 'cleanup_cache')
    def test_get_cache_success(self, mock_cleanup_cache):
        value = self.cache.get_cache(self.session_id, self.other_property)
        self.assertEqual(value, self.other_value)
        caps = self.cache.get_cache(self.session_id, Cache.capabilities)
        self.assertDictEqual(caps, self.capabilities)
        mock_cleanup_cache.assert_called()

    @patch('percy.cache.Cache.CACHE_TIMEOUT', 1)
    def test_cleanup_cache(self):
        cache_timeout = self.cache.CACHE_TIMEOUT
        time.sleep(cache_timeout + 1)
        self.assertIn(self.session_id, self.cache.CACHE)
        self.cache.cleanup_cache()
        self.assertNotIn(self.session_id, self.cache.CACHE)

    def test_cleanup_cache_blocks_concurrent_writers_while_iterating(self):
        cache = self.cache
        writer_blocked = []
        writers = []

        class InterleavingDict(dict):
            # Simulates another thread writing to the cache mid-iteration
            def items(self):
                writer = threading.Thread(
                    target=cache.set_cache,
                    args=('other_session', Cache.capabilities, {}))
                writer.start()
                writers.append(writer)
                writer.join(timeout=0.2)
                writer_blocked.append(writer.is_alive())
                for item in list(super().items()):
                    yield item

        original = cache.CACHE
        cache.CACHE = InterleavingDict(original)
        try:
            cache.cleanup_cache()  # must not raise "dictionary changed size during iteration"
            writers[0].join(timeout=5)
            self.assertEqual(writer_blocked, [True])
            self.assertIn('other_session', cache.CACHE)
        finally:
            cache.CACHE = original
