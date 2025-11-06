import threading

class ThreadSafeSingleton:
    _instances = {}
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super(ThreadSafeSingleton, cls).__new__(cls)
        return cls._instances[cls]