# Audio

Principio: voz, música y efectos **nacen separados** (A1/A2/A3) y se entregan como stems en el bundle.
Nunca se separa una mezcla que podemos evitar.

## Narración

- Producción real: grabación humana autorizada. Se sustituye sin tocar el timeline:
  `explica -p proyecto asset replace voice-01 grabacion.wav` (actualiza hash y duración; `validate` avisa si el
  clip ahora excede la nueva duración).
- Marcador local libre: **Piper TTS** (MIT) con una voz en español. En este PC: `es_MX-ald-medium`
  (dataset Unlicense, 63 MB, 22,05 kHz, resampleado a 48 kHz mono), carga 1,8 s, síntesis ~0,4 s por
  frase, 220 MiB RSS. Descarga manual (no implícita):

  ```bash
  mkdir -p .models/piper && cd .models/piper
  curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/ald/medium/es_MX-ald-medium.onnx
  curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/ald/medium/es_MX-ald-medium.onnx.json
  sha256sum es_MX-ald-medium.onnx   # 019b3803293c93e34a206dd2e53a3889209a514e786fd7144f7b70196c579b63
  ```
- No se clona ninguna voz; no se usa TTS en la nube ni API de pago.

Los captions se cronometran con la **duración medida** de cada clip de voz (una frase = un clip = un caption),
no estimada a partir del texto (A06). Si cambias una frase, vuelve a sintetizar/grabar esa frase: los captions
siguientes se desplazan con `timeline_patch shift`.

## Música

`explica music <id> <segundos> --seed N` sintetiza con FFmpeg (`sine` ×3, trémolo, paso bajo, fades) un
colchón ambiental original y determinista (test de bytes idénticos por semilla). Licencia: CC0 por el
proyecto. Nivel por defecto −20 dB, más `gain_db` del clip (piloto: −6 dB) para no tapar la voz.

## Loudness (A09)

Objetivo del proyecto (no requisito universal de ninguna plataforma): −16 LUFS ±1 integrado, true peak ≤ −1 dBTP.
El bundle aplica `loudnorm` en dos pasadas sobre el master y vuelve a medir con `ebur128`:
piloto → −15,9 LUFS, LRA 2,4 LU, −2,5 dBTP (`docs/evidence/A17-pilot-bundle-QA-REPORT.json`).
`explica qa archivo.mp4` reporta loudness, intervalos negros y silencios (A11) sin borrar nada.

## Referencia externa (análisis, no producción)

Para transcribir un audio de referencia con `whisper.cpp` u otro ASR local hay que instalarlo aparte; este
repo solo **almacena** transcripciones con su procedencia (`transcripts` en el manifiesto) y las sirve por
`transcript_query`. No infiere lo hablado a partir de descripciones ni inventa timestamps.
