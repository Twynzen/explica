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

## Separación de fuentes (A10, `scripts/separation_check.py`)

explica produce stems propios, así que nunca necesita separar su propia mezcla. La prueba A10 responde a otra
pregunta: si llega una **mezcla ajena** (p. ej. una referencia para análisis), ¿puede una herramienta libre y
local recuperar voz y música de forma útil? Como el piloto tiene stems reales, sirven de verdad de referencia:
el script coloca los clips de voz en su posición de timeline con su ganancia, hace lo mismo con la música
(con un desplazamiento opcional para simular una cama más alta), los mezcla, separa la mezcla y mide la SDR de
cada stem estimado contra el real. La mezcla original no se modifica; los archivos quedan para una comparativa
A/B por oído.

Herramienta evaluada: `audio-separator` 0.47.0 (MIT; instala `torch` CPU 2.14 y `onnxruntime` 1.31, 1,3 GB
en un venv aparte de `~/.cache`, nunca en el del proyecto) con el modelo MDX-Net `UVR-MDX-NET-Voc_FT.onnx`
(64 MB, sha256 `534b2070fcc7df514b13ef660dc8cbb328679c2374d04354a5c42bb14ecce111`, distribuido por el
proyecto UVR). Medido en este PC (i3-9100F, CPU): 93 s para 29,4 s de audio, RSS máx 2,7 GiB — es el proceso
más pesado de todo el repositorio y no cabe junto a otro trabajo pesado en 8 GiB.

| Mezcla | Música respecto a la voz | SDR voz (escalada) | Fuga de voz en silencios | SDR música |
|---|---|---|---|---|
| piloto tal cual | −29,5 dB | 10,4 dB (35,1 dB) | −51,6 dB bajo la mezcla | −18,7 dB |
| música +18 dB | −11,5 dB | 11,3 dB (30,7 dB) | −49,4 dB bajo la mezcla | +0,3 dB |

Lectura: la **voz** se recupera limpia (la SDR sin escalar es baja solo porque el modelo devuelve la voz con
otra ganancia; con ganancia ajustada supera los 30 dB, y en los huecos sin voz apenas deja residuo). La
**música** sale aproximada y con artefactos: con la cama a −11,5 dB mejora 12 dB sobre no separar, pero sigue
en torno a 0 dB de SDR; con la cama muy baja el stem es casi todo residuo de voz. Es lo que preveía el plan
(«stems aproximados, con artefactos esperables»): útil para transcribir o analizar una narración, no para
reutilizar la música. Evidencia: `docs/evidence/A10-separation-off0.json` y `-off18.json`. La escucha A/B
humana sigue pendiente.

## Referencia externa (análisis, no producción)

El audio del reel de referencia se transcribió con el mismo procedimiento (muestra de 30 s primero, luego los
69 s; mezcla original intacta; idioma detectado es, p = 0,989). El resultado vive **fuera de este repositorio**
(material privado) con cada segmento marcado como texto ASR, término sospechoso o inferencia visual, y
timestamps del modelo no validados por escucha. Este repo solo **almacena** transcripciones con su procedencia
(`transcripts` en el manifiesto) y las sirve por `transcript_query`. No infiere lo hablado a partir de
descripciones ni inventa timestamps.
