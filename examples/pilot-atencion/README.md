# Piloto: «Atención: un paso»

Vídeo vertical 1080×1920, 30 fps, 29,4 s, español, acromático (propuesta inspirada en una referencia
privada, no una regla global). Explica **una** operación: cómo la última posición de una secuencia de tres
tokens calcula sus pesos de atención y combina los valores. Una cabeza, sin entrenamiento, sin MLP, sin
múltiples cabezas ni capas (se nombran como simplificación al final).

## Construir

```bash
.venv/bin/python -m explica.pilot.attention examples/pilot-atencion --piper-model .models/piper/es_MX-ald-medium.onnx
.venv/bin/explica -p examples/pilot-atencion validate
.venv/bin/explica -p examples/pilot-atencion preview --wait 300      # 540x960
.venv/bin/explica -p examples/pilot-atencion export bundle           # master + fuentes + QA
```

Sin `--piper-model` el proyecto se crea con duraciones de marcador y sin pista A1 (CI).

## Guion (una frase = un clip de voz = un caption)

| Beat | Frase | Escena (`scenes/`) |
|---|---|---|
| 1 | Tres tokens de juguete: El, gato, duerme. / ¿A qué atiende la última posición? | `beat1.json`: tokens con posiciones 1–3, nota de máscara causal |
| 2 | Cada token aporta una clave, k; «duerme» aporta la consulta, q. | `beat2.json`: vectores k₁..k₃ bajo cada token, q sobre «duerme», d_k = 2 |
| 3 | Puntuación: q por k, entre raíz de d sub k. / Softmax las convierte en pesos que suman uno. | `beat3.json`: fórmula, s₁..s₃, barras w₁..w₃, arcos con grosor ∝ peso, suma = 1,00 |
| 4 | Cada peso multiplica un valor, v; la suma es la salida de «duerme». | `beat4.json`: v₁..v₃, × wᵢ, flechas, vector salida |
| 5 | Una cabeza simplificada; el modelo la repite en muchas capas. | `beat5.json`: recuadro de la cabeza, pila de capas, zoom out |

## Números (calculados en `explica/pilot/attention_math.py`, verificados en `tests/test_pilot.py`)

- Tokens: El, gato, duerme. d_k = d_v = 2. Máscara causal: la posición 3 mira 1, 2 y 3.
- q₃ = [1, 1]; k₁ = [1, 0], k₂ = [0,5, 1], k₃ = [0, 1]; v₁ = [1, 0], v₂ = [0, 1], v₃ = [0,5, 0,5].
- sᵢ = q·kᵢ/√2 → [0,707, 1,061, 0,707]. wᵢ = softmax(s) → [0,292, 0,416, 0,292] (suma exacta 1; con dos
  decimales 0,29 + 0,42 + 0,29 = 1,00).
- salida = Σ wᵢvᵢ = [0,438, 0,562] → se muestra [0,44, 0,56].

Omisiones explícitas (dichas o rotuladas): números de juguete, una sola cabeza, sin proyección de salida,
sin posición ni normalización, sin entrenamiento. No se afirma que los pesos de atención sean una explicación
causal del modelo.

## Fuentes conceptuales

- Vaswani et al., *Attention Is All You Need*, 2017 — https://arxiv.org/abs/1706.03762
- 3Blue1Brown, *Attention in transformers, step-by-step* — https://www.3blue1brown.com/lessons/attention

Ningún asset, audio ni guion procede de la referencia privada analizada en la fase de investigación.

## Editar

- Cambiar un número o rótulo: `explica -p examples/pilot-atencion scene patch beat3 --ops-json '[{"op":"set","path":"objects.title.text","value":"…"}]'`.
- Cambiar un corte o nivel: `timeline-patch` o Kdenlive + `sync-from-kdenlive`.
- Sustituir la voz: `asset replace voice-03 mi-grabacion.wav`, luego `validate` (si la frase dura distinto,
  `timeline-patch` con `shift`).

Lo que se versiona aquí: `project.json`, `scenes/*.json`, `PILOT-SUMMARY.json`, este README. Renders, audio
generado, checkpoints y exportaciones se regeneran con los comandos de arriba (están en `.gitignore`).
