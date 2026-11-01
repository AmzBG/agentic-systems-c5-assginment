from smoke_test import verify


async def test_real_stdio_round_trip(tmp_path):
    await verify(tmp_path / "pickup.sqlite3")
