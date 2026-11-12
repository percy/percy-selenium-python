from percy.cache import Cache

class DriverMetaData:
    def __init__(self, driver):
        self.driver = driver

    @property
    def session_id(self):
        return self.driver.session_id

    @property
    def command_executor_url(self):
        # Read from the live driver on every call and never cache it: the URL
        # can embed Grid Basic-Auth credentials, and the session cache is
        # process-wide and keyed only by session_id.
        try:
            return self.driver.command_executor._url  # pylint: disable=W0212
        except (AttributeError, Exception):
            return self.driver.command_executor.client_config.remote_server_addr

    @property
    def capabilities(self):
        caps = Cache.get_cache(self.session_id, Cache.capabilities)
        if caps is None:
            caps = dict(self.driver.capabilities)
            Cache.set_cache(self.session_id, Cache.capabilities, caps)
            return caps
        return caps
