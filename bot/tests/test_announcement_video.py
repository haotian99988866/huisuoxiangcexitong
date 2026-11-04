import hashlib
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import imageio_ffmpeg

import announcement_video as service
import api_server


class AnnouncementVideoTests(unittest.TestCase):
    def test_atom_detection_extended_lengths_and_invalid_bounds(self):
        def atom(kind, content=b""):
            return struct.pack(">I4s", 8 + len(content), kind) + content
        with TemporaryDirectory() as directory:
            path = Path(directory) / "1-1.mp4"
            for content, expected in [
                (atom(b"ftyp") + atom(b"moov") + atom(b"mdat"), True),
                (atom(b"ftyp") + atom(b"mdat") + atom(b"moov"), False),
                (struct.pack(">I4sQ", 1, b"free", 16) + atom(b"moov"), True),
            ]:
                path.write_bytes(content)
                self.assertEqual(service.fast_start(path), expected)
            for content in [b"bad", struct.pack(">I4s", 2, b"free"),
                            struct.pack(">I4s", 100, b"free"), struct.pack(">I4s", 1, b"free")]:
                path.write_bytes(content)
                with self.assertRaises(ValueError):
                    service.fast_start(path)

    def test_real_lossless_preparation_ranges_and_reuse(self):
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        with TemporaryDirectory() as directory:
            source = Path(directory) / "1-1.mp4"
            subprocess.run([executable, "-nostdin", "-loglevel", "error", "-f", "lavfi",
                            "-i", "testsrc=size=64x64:rate=5", "-t", "1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                           check=True, capture_output=True, timeout=30)
            original = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertFalse(service.fast_start(source))
            prepared = service.prepare(source)
            self.assertNotEqual(prepared, source)
            self.assertTrue(service.fast_start(prepared))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
            self.assertEqual(service.playback_path(source), prepared)
            with patch.object(service.subprocess, "run") as run:
                self.assertEqual(service.prepare(source), prepared)
                run.assert_not_called()
            def packets(path):
                return subprocess.run(
                    [executable, "-nostdin", "-loglevel", "error", "-i", str(path),
                     "-map", "0", "-c", "copy", "-f", "hash", "-hash", "sha256", "-"],
                    check=True, capture_output=True, timeout=30).stdout
            self.assertEqual(packets(source), packets(prepared))
            with patch("announcements.media_file", return_value={"kind": "video", "filename": source.name}), \
                    patch("announcements.local_media_path", return_value=source):
                client = api_server.app.test_client()
                with client.get("/announcement-media/1/0", headers={"Range": "bytes=0-63"}) as response:
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(response.data, prepared.read_bytes()[:64])
                    self.assertEqual(response.headers["X-Video-Prepared"], "faststart")
                    self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                with client.head("/announcement-media/1/0") as response:
                    self.assertEqual(int(response.headers["Content-Length"]), prepared.stat().st_size)
                with client.get("/announcement-media/1/0", headers={"Range": "bytes=-32"}) as response:
                    self.assertEqual(response.data, prepared.read_bytes()[-32:])
            source.write_bytes(source.read_bytes() + b"\x00")
            self.assertEqual(service.playback_path(source), source)

    def test_already_streamable_and_webm_do_not_remux(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "1-1.mp4"
            path.write_bytes(struct.pack(">I4s", 8, b"moov"))
            webm = Path(directory) / "1-2.webm"
            webm.write_bytes(b"webm")
            with patch.object(service.subprocess, "run") as run:
                self.assertEqual(service.prepare(path), path)
                self.assertEqual(service.prepare(webm), webm)
                self.assertEqual(service.playback_path(webm), webm)
                run.assert_not_called()

    def test_failed_conversion_keeps_original_and_no_prepared_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "1-1.mp4"
            path.write_bytes(struct.pack(">I4s", 8, b"mdat") + struct.pack(">I4s", 8, b"moov"))
            before = path.read_bytes()
            with patch.object(service.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaises(RuntimeError):
                    service.prepare(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(service.playback_path(path), path)
