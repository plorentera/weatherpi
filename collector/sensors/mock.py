import math
import random
import time
from typing import Dict, Any
from .base import SensorDriver

class MockSensorDriver(SensorDriver):
    def __init__(self, seed: int = 1234):
        random.seed(seed)
        self.t0 = time.time()

    def read(self) -> Dict[str, Any]:
        t = time.time() - self.t0

        temp = 20.0 + 2.5 * math.sin(t / 60.0) + random.uniform(-0.2, 0.2)
        hum = 55.0 + 8.0 * math.sin(t / 90.0 + 1.0) + random.uniform(-0.5, 0.5)
        pres = 1013.0 + 1.5 * math.sin(t / 300.0 + 2.0) + random.uniform(-0.2, 0.2)

        hum = max(0.0, min(100.0, hum))

        return {
            "temp_c": round(temp, 2),
            "humidity_pct": round(hum, 2),
            "pressure_hpa": round(pres, 2),
        }
