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
piloto → −16,0 LUFS, LRA 3,2 LU, −3,3 dBTP (`docs/evidence/A17-pilot-bundle-QA-REPORT.json`).
`explica qa archivo.mp4` reporta loudness, intervalos negros y silencios (A11) sin borrar nada.

## Comprobación automática de la narración (`scripts/voice_check.py`)

Transcribe cada clip de voz con `faster-whisper` (modelo `base`, int8, CPU) y lo compara con su guion
(`similarity` por `difflib`). Es evidencia de inteligibilidad **por máquina** con un juez débil (el modelo
base confunde palabras incluso en voz clara) y Piper no es determinista, así que los valores oscilan entre
ejecuciones; úsalo para detectar términos mal pronunciados (letras, símbolos), no como aprobación. En el piloto,
el guion de TTS deletrea las letras («ka», «cu», «uve», «de sub ka») mientras el caption muestra `k`, `q`, `v`,
`d sub k`; antes de ese cambio las frases con letras bajaban a 0,70 de similitud. Resultado de la última
ejecución: `docs/evidence/pilot-voice-check.json`. La escucha humana sigue pendiente.

Medido en este PC (i3-9100F, CPU, 2 hilos): `faster-whisper` 1.2.1 + CTranslate2 4.8.3, modelo base int8:
carga 0,6 s, 69 s de audio en 8,2 s, RSS máx 483 MiB. PyAV 16 rompe `decode_audio` de faster-whisper 1.2.1
(`metadata_errors`), por eso el script decodifica el WAV con `wave`/NumPy y pasa el array.

## Referencia externa (análisis, no producción)

El audio del reel de referencia se transcribió con el mismo procedimiento (muestra de 30 s primero, luego los
69 s; mezcla original intacta; idioma detectado es, p = 0,989). El resultado vive **fuera de este repositorio**
(material privado) con cada segmento marcado como texto ASR, término sospechoso o inferencia visual, y
timestamps del modelo no validados por escucha. Este repo solo **almacena** transcripciones con su procedencia
(`transcripts` en el manifiesto) y las sirve por `transcript_query`. No infiere lo hablado a partir de
descripciones ni inventa timestamps.
