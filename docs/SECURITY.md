# Seguridad

Modelo: un agente (posiblemente alimentado con texto hostil de transcripciones, OCR o nombres de archivo)
controla el proyecto. Nada de lo que recibe el servidor debe ejecutarse ni salir de la raíz del proyecto.

| Vector | Medida | Test |
|---|---|---|
| `../`, rutas absolutas, symlinks hacia fuera | `paths.resolve_inside`: comprobación léxica y física contra la raíz | `tests/test_security.py` |
| IDs con metacaracteres | `check_id` (`[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`) | idem |
| Texto «ignora instrucciones», `$(…)`, `<script>` | es un valor JSON; se dibuja como glifos con cairo; nunca pasa por shell ni eval | idem |
| Subprocesos | siempre argv (`subprocess.run([...])`), entorno mínimo (`clean_env`: sin tokens, `LC_NUMERIC=C`, `QT_QPA_PLATFORM=offscreen`) | idem |
| Ejecución de código | no hay herramienta `execute_*`; las escenas son datos, no Python; no hay TeX (ni shell-escape) | diseño |
| Red | solo stdio; ninguna llamada HTTP en el código; descargas de modelos son manuales y documentadas | diseño |
| Cancelación de jobs | solo pids lanzados por nosotros y cuya `/proc/<pid>/cmdline` coincide | `jobs.py` |
| Escrituras concurrentes | lock por pid + `expected_revision` + snapshot previo | `tests/test_project.py` |
| Datos privados | el repo no incluye vídeo/audio de terceros; logs de evidencia con rutas anonimizadas | revisión manual |

Fuera de alcance: aislamiento de recursos (cgroups), límites de CPU/RAM del render (se serializa por
convención: un trabajo pesado a la vez, ver `limits` en `project_inspect`).
