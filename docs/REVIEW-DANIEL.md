# Revisión humana pendiente (A16, A08, A09)

Estas tres comprobaciones no las puede cerrar un agente. Tiempo estimado: 5 minutos.

## 1. Ver el piloto (A16)

```bash
mpv examples/pilot-atencion/export/bundle-r17/pilot-atencion-master.mp4     # 29,4 s, vertical
```

Después de verlo una vez, contesta por escrito (en este archivo o en el STATE.md de la tarea):

1. ¿Qué calcula la posición 3 («duerme») y con qué? (respuesta esperada: puntuaciones q·kᵢ/√d_k con las claves
   de las tres posiciones, softmax → pesos que suman 1, suma ponderada de los valores vᵢ).
2. ¿Qué elemento visual no entendiste o cambiarías? (texto, número, ritmo, orden).
3. ¿Qué frase de la narración sobra o falta?

## 2. Legibilidad en móvil (A08)

Abre `docs/evidence/pilot-master-f255.png` y `pilot-master-f600.png` en el teléfono a pantalla completa.
¿Se leen captions, números y fórmulas sin ampliar? Sí / No / cuáles no.

## 3. Audio (A09)

Escucha el master con auriculares. ¿La voz se entiende en todas las frases? ¿La música tapa la voz en algún
momento? Frases dudosas conocidas: las que contienen «k» y «q» (voz sintética de prueba).

## 4. Opcional: A/B de separación de fuentes (A10)

Ya está medida por máquina (`docs/AUDIO.md` §Separación). Si quieres oírla: ejecuta `scripts/separation_check.py`
como indica su cabecera y compara `truth-voice.wav` con `separated/mix_(Vocals)_….wav` (y lo mismo para la
música). Anota si la voz separada sirve para transcribir y si la música te parece reutilizable (esperado: no).

## Cómo aplicar una corrección

- Rótulo o número: `.venv/bin/explica -p examples/pilot-atencion scene patch beat3 --ops-json '[{"op":"set","path":"objects.title.text","value":"Nuevo rótulo"}]'`
- Corte o nivel: abre `examples/pilot-atencion/export/pilot-atencion.kdenlive` en Kdenlive, guarda, y luego
  `.venv/bin/explica -p examples/pilot-atencion sync-from-kdenlive examples/pilot-atencion/export/pilot-atencion.kdenlive`
- Voz propia: graba la frase y `.venv/bin/explica -p examples/pilot-atencion asset replace voice-03 grabacion.wav`
- Regenerar entrega: `.venv/bin/explica -p examples/pilot-atencion export bundle`

Cuando respondas, actualiza la fila A16 (y A08/A09) en `docs/ACCEPTANCE.md` con tu veredicto y la fecha.
