from abc import ABC, abstractmethod


class VisionProvider(ABC):
    @abstractmethod
    def observe(self, plate_id: str, food_state: str) -> dict:
        """Return one normalized plate observation."""


class MockVisionProvider(VisionProvider):
    def observe(self, plate_id: str, food_state: str) -> dict:
        if plate_id not in {"A", "B"}:
            raise ValueError("plate_id must be A or B")
        if food_state not in {"food", "empty", "unknown"}:
            raise ValueError("food_state must be food, empty, or unknown")
        return {"plate_id": plate_id, "food_state": food_state, "source": "mock"}


class RealVisionProvider(VisionProvider):
    def observe(self, plate_id: str, food_state: str) -> dict:
        raise NotImplementedError("Real vision inference is not implemented; use VISION_PROVIDER=mock")


def create_vision_provider(name: str) -> VisionProvider:
    if name == "mock":
        return MockVisionProvider()
    if name == "real":
        raise NotImplementedError("VISION_PROVIDER=real is not implemented; use VISION_PROVIDER=mock")
    raise ValueError("VISION_PROVIDER must be 'mock' or 'real'")
