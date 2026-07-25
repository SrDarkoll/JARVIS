# Third-Party Notices

J.A.R.V.I.S. source code is licensed under the repository `LICENSE`. That
license does not replace or override the licenses of the third-party runtime
and voice assets listed below.

This file records the provenance information available from each upstream
source. It is provided for attribution and transparency and is not legal
advice. Downstream distributors remain responsible for evaluating whether
their intended use satisfies every applicable license.

## Bundled Piper Voice Models

The Windows release and Git LFS checkout distribute the following ONNX model
weights and matching configuration files from
[`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices).
J.A.R.V.I.S. does not claim authorship of these assets.

### `en_GB-northern_english_male-medium`

- Bundled files:
  - `models/en_GB-northern_english_male-medium.onnx`
  - `models/en_GB-northern_english_male-medium.onnx.json`
- Upstream model:
  <https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_GB/northern_english_male/medium>
- SHA-256 of the bundled ONNX file:
  `57a219ae8e638873db7d18893304be5069c42868f392bb95c3ff17f0690d0689`
- Upstream repository metadata identifies `rhasspy/piper-voices` as MIT.
- The upstream model card identifies the training dataset as OpenSLR 83 and
  declares `CC-BY-SA 4.0 International`.
- Dataset source: <http://www.openslr.org/83/>
- License text:
  `third_party/licenses/CC-BY-SA-4.0.txt`
- Upstream model card:
  `third_party/model_cards/en_GB-northern_english_male-medium.md`
- J.A.R.V.I.S. modification status: the model and configuration are
  redistributed without modification.

#### Lessac provenance notice

The upstream model card states that this voice was fine-tuned from the U.S.
English Lessac voice. It does not identify the license of that base voice in
the model card. The attribution above records the information supplied by the
upstream source, but it does not resolve whether the Lessac training lineage
creates additional obligations or restrictions.

### `es_MX-claude-high`

- Bundled files:
  - `models/es_MX-claude-high.onnx`
  - `models/es_MX-claude-high.onnx.json`
- Upstream model:
  <https://huggingface.co/rhasspy/piper-voices/tree/main/es/es_MX/claude/high>
- SHA-256 of the bundled ONNX file:
  `3ef40a71ea63852cd8ab7e6fa7d2ecdcfa67a0b47c9c48e3f10e02ee02083ea0`
- Upstream repository metadata identifies `rhasspy/piper-voices` as MIT.
- The upstream model card identifies the dataset/training source as
  `HirCoir/Piper-TTS-Spanish` and declares `Apache-2.0`.
- Dataset/training source:
  <https://huggingface.co/spaces/HirCoir/Piper-TTS-Spanish>
- License text: `third_party/licenses/Apache-2.0.txt`
- Upstream model card: `third_party/model_cards/es_MX-claude-high.md`
- J.A.R.V.I.S. modification status: the model and configuration are
  redistributed without modification.

Repository-level Piper voice metadata and source links are preserved in
`third_party/PIPER_VOICES_REPOSITORY_NOTICE.md`.

## Piper Runtime

`requirements.txt` installs `piper-tts==1.6.0` from PyPI during setup. The
runtime is not stored in this source repository or the Windows ZIP before
installation. Its package metadata declares `GPL-3.0-or-later`.

- Package: <https://pypi.org/project/piper-tts/1.6.0/>
- Source: <https://github.com/OHF-Voice/piper1-gpl>
- License text: `third_party/licenses/GPL-3.0.txt`

The presence of this GPL runtime dependency does not change the labels on the
separately distributed voice datasets. It may create additional obligations
for combined redistribution; downstream distributors should evaluate those
obligations independently.
