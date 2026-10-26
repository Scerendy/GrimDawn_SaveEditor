"""加密层的测试（不需要真实存档文件）。"""
import unittest

from gd_save.crypto import (MAGIC, Decoder, Encoder, MASK, translation_table)


class TranslationTableTest(unittest.TestCase):
    def test_first_entries_match_the_documented_formula(self):
        seed = 0x5F13A784
        table = translation_table(seed)
        state = (seed ^ 0x55555555) & MASK
        first = ((state >> 1) | (state << 31)) & MASK
        self.assertEqual(table[0], (first * 39916801) & MASK)
        second = ((table[0] >> 1) | (table[0] << 31)) & MASK
        self.assertEqual(table[1], (second * 39916801) & MASK)
        self.assertEqual(len(table), 256)

    def test_table_is_deterministic(self):
        self.assertEqual(translation_table(1234), translation_table(1234))
        self.assertNotEqual(translation_table(1234), translation_table(1235))


class StreamRoundTripTest(unittest.TestCase):
    def test_magic_roundtrip(self):
        enc = Encoder.for_archive(0xDEADBEEF)
        enc.u32(MAGIC)
        data = enc.data
        dec = Decoder(data)
        self.assertEqual(dec.u32(), MAGIC)

    def test_values_roundtrip(self):
        enc = Encoder.for_archive(0x01234567)
        values32 = [0, 1, 0xFFFFFFFF, 0x80000000, 123456789, 42]
        for value in values32:
            enc.u32(value)
        for value in (0, 7, 200, 255):
            enc.byte(value)
        enc.f32(3.5)
        enc.f32(-0.25)
        enc.ascii_string("records/items/lootaffixes/prefix")
        enc.utf16_string("神秘字符 Ünïcode")
        enc.uuid("0123456789abcdef0123456789abcdef")
        raw = [0, 1, 2, 250, 255]
        enc.blob(bytes(raw))
        enc.bare_state(0xCAFEBABE)
        enc.u32(0x1234)
        data = enc.data

        dec = Decoder(data)
        for value in values32:
            self.assertEqual(dec.u32(), value)
        for value in (0, 7, 200, 255):
            self.assertEqual(dec.byte(), value)
        self.assertAlmostEqual(dec.f32(), 3.5, places=6)
        self.assertAlmostEqual(dec.f32(), -0.25, places=6)
        self.assertEqual(dec.ascii_string(), "records/items/lootaffixes/prefix")
        self.assertEqual(dec.utf16_string(), "神秘字符 Ünïcode")
        self.assertEqual(dec.uuid(), "0123456789abcdef0123456789abcdef")
        self.assertEqual(list(dec.blob(5)), raw)
        self.assertEqual(dec.bare_state(), 0xCAFEBABE)
        self.assertEqual(dec.u32(), 0x1234)

    def test_bare_state_resets_decoding(self):
        """裸状态使它之后的所有内容与之前的内容彼此独立。"""
        first = Encoder.for_archive(1111)
        first.bare_state(0x5A5A5A5A)
        first.u32(0xAABBCCDD)
        second = Encoder.for_archive(2222)
        second.bare_state(0x5A5A5A5A)
        second.u32(0xAABBCCDD)
        self.assertEqual(first.data[8:], second.data[8:])

    def test_size_field_does_not_mix_the_state(self):
        enc = Encoder.for_archive(99)
        state_at_size = enc.state
        offset = enc.reserve(4)
        enc.byte(1)
        enc.patch_u32_at(offset, state_at_size, 7)
        data = enc.data
        dec = Decoder(data)
        self.assertEqual(dec.size_u32(), 7)
        # size 字段之后的状态必须与它之前的状态相同
        self.assertEqual(dec.state, state_at_size)
        self.assertEqual(dec.byte(), 1)


if __name__ == "__main__":
    unittest.main()
