import unittest
from music_metadata import identify


def caption(title):
    return f"🔍 / 今日歌单 / · {title} ☁️"


class MusicMetadataTests(unittest.TestCase):
    def test_real_artist_title_orders_use_caption_as_anchor(self):
        self.assertEqual(identify("Justin Bieber - Come Around Me (Audio)", "<unknown>", "",
                                  caption("Come Around Me")), ("Come Around Me", "Justin Bieber"))
        self.assertEqual(identify("可能 - 李怡然同學『歌词』【動態歌詞】", "<unknown>", "",
                                  caption("可能")), ("可能", "李怡然同學"))
        self.assertEqual(identify("Mike Posner - Please Don't Go (Lyrics)",
                                  "yeah you got me begging baby please don't go", "",
                                  caption("Please Don't Go")), ("Please Don't Go", "Mike Posner"))

    def test_bracketed_title_and_decorated_artist(self):
        self.assertEqual(identify("薛之謙 Joker Xue【那是你離開了北京的生活】HD 高清官方完整版 MV",
                                  "<unknown>", "", caption("那是你离开了北京的生活")),
                         ("那是你离开了北京的生活", "薛之謙 Joker Xue"))
        self.assertEqual(identify("菊花台", "周杰倫 Jay Chou【菊花台 Chrysanthemum Terrace】"),
                         ("菊花台", "周杰倫 Jay Chou"))

    def test_original_filename_recovers_missing_artist(self):
        self.assertEqual(identify("兰亭序", "<unknown>", "兰亭序 周杰伦 (歌词版)_256k.mp3",
                                  caption("兰亭序")), ("兰亭序", "周杰伦"))
        self.assertEqual(identify("7. 和你   余佳运", "<unknown>", "7. 和你   余佳运.mp3",
                                  caption("和你")), ("和你", "余佳运"))
        self.assertEqual(identify("SoMo_50_Feet_lyrics_G7F1eEKhvrs_250", "<unknown>",
                                  "SoMo_50_Feet_lyrics_G7F1eEKhvrs_250.ogg", caption("50 Feet")),
                         ("50 Feet", "SoMo"))

    def test_versions_preserved_and_unknown_not_guessed(self):
        self.assertEqual(identify("李恩澤 - 求佛（RnB版）『歌词』", "<unknown>", "", caption("求佛")),
                         ("求佛（RnB版）", "李恩澤"))
        self.assertEqual(identify("雨天", "<unknown>", "雨天.mp3", caption("雨天")),
                         ("雨天", "未知歌手"))
        self.assertEqual(identify("Hurt - NewJeans", "<unknown>"),
                         ("Hurt - NewJeans", "未知歌手"))
        self.assertEqual(identify("Live Forever", "Artist"), ("Live Forever", "Artist"))

    def test_valid_metadata_and_plain_document_fallback(self):
        self.assertEqual(identify("Test song", "Test artist", "song.wav"), ("Test song", "Test artist"))
        self.assertEqual(identify(None, None, "song.wav"), ("song", "未知歌手"))
        self.assertEqual(identify("无畏", "少年说唱企划 EP03 _ iQiyi精选"), ("无畏", "未知歌手"))
        self.assertEqual(identify(None, None), ("未命名歌曲", "未知歌手"))

    def test_real_preview_regressions_do_not_replace_known_singers_with_guest_or_song(self):
        for title, artist, filename, expected_title in [
            ("Hold On - Ft Stan & D1ne (Official Video)", "Merczz", "", "Hold On"),
            ("Closer (Lyrics) ft. Halsey", "The Chainsmokers", "", "Closer"),
            ("一笑傾城", "汪蘇瀧", "汪蘇瀧_一笑傾城《微微一笑很傾城片頭曲》.mp3", "一笑倾城"),
            ("Dusk Till Dawn (Lyrics)", "ZAYN & Sia", "", "Dusk Till Dawn"),
        ]:
            with self.subTest(title=title):
                anchor = "Dusk Till Down" if title.startswith("Dusk") else expected_title
                self.assertEqual(identify(title, artist, filename, caption(anchor)), (expected_title, artist))

    def test_live_filename_is_a_version_not_an_artist(self):
        self.assertEqual(identify("触碰不到的你", "<unknown>", "触碰不到的你 (Live)_256k.mp3",
                                  caption("触碰不到的你")), ("触碰不到的你(Live)", "未知歌手"))
