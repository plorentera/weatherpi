from typing import Dict, Any

class SensorDriver:
    def read(self) -> Dict[str, Any]:
        raise NotImplementedError
