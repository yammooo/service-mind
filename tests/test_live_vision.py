import unittest

import numpy as np

from backend.live_vision import COCO, decode_predictions


class LiveVisionTests(unittest.TestCase):
    def test_decodes_yolov8_class_and_filters_low_confidence(self):
        self.assertEqual(len(COCO), 80)
        output = np.zeros((1, 84, 3), dtype=np.float32)
        output[0, :4, 0] = [320, 240, 100, 80]
        output[0, 4 + 41, 0] = 0.91
        output[0, :4, 1] = [100, 100, 20, 20]
        output[0, 4 + 2, 1] = 0.1
        output[0, :4, 2] = [320, 240, 100, 80]
        output[0, 4 + 39, 2] = 0.92

        found = decode_predictions(output)

        self.assertEqual({item[2] for item in found}, {"cup", "bottle"})
        self.assertEqual(found[0][0], [270.0, 200.0, 100.0, 80.0])


if __name__ == "__main__":
    unittest.main()
