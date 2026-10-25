# Upstream format fixtures

Files copied from Equim-chan/mjai-reviewer, commit `2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9`, directory `convlog/tests/testdata/`, on 2026-10-25. Apache License 2.0 is included as LICENSE. Copyright and attribution remain with the upstream contributors. No upstream strategy engine or model weights are bundled.

| File | Upstream Git blob SHA | Purpose |
|---|---|---|
| rinshan.json | 6d1a38cee907d4dd347d900b9b164099d4f5a1e5 | Accepted real tenhou.net/6 structure; riichi, ankan, supplementary draw |
| chankan.json | 4ef8a8b5754123db5ae6504917dbc537fbfce558 | Accepted structure with robbed added kan |
| four_reach.json | 69035ced62bd070d91ee559aafc479c6b2f7ec34 | Accepted four-riichi abortive ending |
| ryukyoku.json | 412ce22a266c10c6a323463b9937b878d7700fe0 | Currently unsupported reconstruction; explicit rejection test |

These are upstream Tenhou-compatible fixtures, not newly fetched Mahjong Soul records, not this user's matches, and not proof that the current Mahjong Soul client or share-link retrieval works. The adapter's independent implementation is informed by upstream schema/encoding documentation. No external converter executable was installed or run.

Sources:

- https://github.com/Equim-chan/mjai-reviewer/tree/2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9/convlog/tests/testdata
- https://github.com/Equim-chan/mjai-reviewer/blob/2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9/convlog/src/mjai.rs
- https://github.com/Equim-chan/mjai-reviewer/blob/2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9/convlog/src/tenhou/json_scheme.rs
- https://github.com/gimite/mjai (standard mjai event family)
