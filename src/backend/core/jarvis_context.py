import logging
import threading


class JarvisContext:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.deps = {}
                cls._instance.logger = logging.getLogger("JARVIS")
        return cls._instance

    def set(self, key, value):
        self.deps[key] = value

    def get(self, key, default=None):
        return self.deps.get(key, default)

    def update(self, mapping):
        self.deps.update(mapping)

    def __getitem__(self, key):
        return self.deps[key]

    def __setitem__(self, key, value):
        self.deps[key] = value

context = JarvisContext()



