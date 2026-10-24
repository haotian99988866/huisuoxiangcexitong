import io
import unittest

from PIL import Image

from image_hash import ImageDeduper, compute_hash, distance


def _image(pattern, size=(200, 200)):
    img = Image.new("RGB", size)
    px = img.load()
    for x in range(size[0]):
        for y in range(size[1]):
            px[x, y] = pattern(x / size[0], y / size[1])
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def gradient(u, v):
    value = int(255 * u)
    return (value, value // 2, 40)


def stripes(u, v):
    value = 255 if int(u * 9) % 2 else 0
    return (value, value, value)


class ImageHashTest(unittest.TestCase):
    def test_same_image_resized_is_close(self):
        a = compute_hash(_image(gradient))
        b = compute_hash(_image(gradient, (120, 120)))
        self.assertLessEqual(distance(a, b), 4)

    def test_different_images_are_far(self):
        a = compute_hash(_image(gradient))
        b = compute_hash(_image(stripes))
        self.assertGreater(distance(a, b), 4)

    def test_invalid_bytes(self):
        self.assertIsNone(compute_hash(b"not an image"))

    def test_deduper(self):
        a = compute_hash(_image(gradient))
        b = compute_hash(_image(gradient, (120, 120)))
        c = compute_hash(_image(stripes))
        deduper = ImageDeduper()
        self.assertEqual(deduper.key_for(a, 1), 1)
        self.assertEqual(deduper.key_for(b, 2), 1)
        self.assertEqual(deduper.key_for(c, 3), 3)
        self.assertEqual(deduper.key_for(None, 4), 4)


if __name__ == "__main__":
    unittest.main()
