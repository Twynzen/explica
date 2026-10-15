# Límites y pendientes (lo que NO está comprobado)

1. **GUI de Kdenlive.** No se abrió el proyecto exportado en Kdenlive 26.08 con pantalla. El intento con
   `QT_QPA_PLATFORM=offscreen` termina en *segmentation fault*; el backtrace del core dump
   (`coredumpctl`) sitúa el fallo en `KColorButton::KColorButton → QMimeData::hasColor` (KF6WidgetsAddons,
   portapapeles inexistente en la plataforma offscreen), es decir, en la construcción de la interfaz y no en la
   carga del documento (sin archivo, Kdenlive se queda esperando en su asistente inicial sin crash). Verificación pendiente por una persona: abrir `export/pilot-atencion.kdenlive`,
   comprobar que los 23 productores cargan (clips de escena, 7 wav, títulos, captions), mover un clip, editar un
   título, guardar y ejecutar `explica sync-from-kdenlive`. El formato sigue el de los proyectos del repositorio
   de pruebas de Kdenlive (doc 1.1) y `melt` lo renderiza.
2. **Propiedades Kdenlive no verificadas en GUI:** `kdenlive:clip_type` (0 AV / 1 audio / 2 vídeo, inferido de
   muestras), `kdenlive:producer_type`, ausencia de `meta.media.*` (Kdenlive las regenera al cargar, según
   su código fuente, pero no se ha observado).
3. **Audio no escuchado por humanos.** Ningún agente oyó la narración ni la mezcla: solo medidas (LUFS, true
   peak, silencios), texto y una comprobación por máquina (`scripts/voice_check.py`: ASR sobre cada clip; dos de
   siete clips quedaron bajo 0,85 de similitud en la última ejecución, con un juez débil). La voz es un marcador
   sintético; la producción debe reemplazarla por una grabación. El balance voz/música requiere escucha humana.
4. **Transcripción del reel de referencia:** hecha con faster-whisper base en CPU (privada, fuera del repo),
   **sin revisión humana**: varios términos marcados como sospechosos. `whisper.cpp` no se compiló (falta `cmake`).
5. **Transparencia** probada solo con QuickTime RLE (`qtrle`, argb) → `qtblend` en MLT. Otros codecs con alfa
   (ProRes 4444, VP9 alpha) no probados.
6. **Fuentes:** el motor usa la API de texto simple de cairo (sin Pango): no hay fallback de glifos entre fuentes.
   Caracteres fuera de Liberation Sans (p. ej. `⋯`, `✓`) salen como cuadro; usa `…`, `×`, `Σ`, `√`, flechas y
   griego, que sí existen. No hay LaTeX: fórmulas con el mini-markup `_{}`, `^{}`, `√{}`.
7. **Sin Pango = sin shaping complejo** (árabe, devanagari). Texto latino/griego/cirílico correcto.
8. **Rendimiento** medido solo en un i3-9100F / 7,7 GiB. No hay límites de recursos impuestos: un trabajo
   pesado a la vez es una convención, no un cgroup.
9. **Kdenlive subtítulos nativos** no se usan (captions = clips de título en V4 + SRT). Si prefieres la pista de
   subtítulos de Kdenlive, importa el SRT exportado desde la GUI.
10. **CI** ejecuta pruebas con ffmpeg/melt de Ubuntu y construye el piloto **sin voz** (no descarga modelos).
11. **Windows/macOS:** no probado; el código evita rutas específicas de Linux salvo `/proc/<pid>/cmdline` en la
    verificación de jobs (degrada a «no coincide» en otros sistemas).
