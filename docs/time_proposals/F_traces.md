# F. Propuestas de velocidad a partir de las trazas (V23 frente a V22)

Lo que miden las trazas: las 10 de V23 (el sistema actual) y las 10 de V22, llamada por llamada. Para cada llamada al
modelo calculé la duración como su `elapsed_s` menos el final del evento anterior (incluye el prefill, que es pequeño). Cada
llamada se clasifica según el agente y su tipo: decisión, `open`, respuesta final que copia una línea, despacho o ruta del
coordinador. El ritmo calculado coincide con el del brief: 85.934 tokens en 2.404 s, es decir, **35,8 tok/s**. La
generación ocupa el 89 % del tiempo de agente, los scripts el 8 % (228 s) y el arranque del harness el 2 % (62 s).
(Una peculiaridad de las trazas: cuando el coordinador emite dos llamadas en paralelo, el `open` de scope queda dentro del
paso del coordinador. Lo he contado como coordinador, y son unos 2 s por tarea.)

## 1. Dónde se fue el tiempo en V23 (segundos de generación por tarea)

Las columnas por agente cuentan solo sus **decisiones**. «open+final» son las llamadas mecánicas de los sub-agentes:
abrir la vista y copiar la línea de respuesta.

| tarea | resultado | fin (s) | arranque | requirements | locate | scope | specify | edit | review | open+final | coordinador | scripts | 1.er edit (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fastapi_14448 | no resuelta (5 min) | 296 | 7 | 62 | 68 | 16 | 60 | 0 | 0 | 31 | 22 | 31 | — |
| fastapi_14583 | no puntuable (5 min) | 294 | 9 | 10 | 16 | 15 | 134 | 35 | 0 | 32 | 17 | 25 | 283 |
| fastapi_14851 | resuelta | 302 | 9 | 21 | 10 | 0 | 114 | 46 | 0 | 30 | 51 | 21 | 239 |
| fastapi_14986 | no resuelta (5 min) | 298 | 11 | 28 | 13 | 15 | 100 | 0 | 0 | 61 | 46 | 24 | — |
| fastapi_15800 | no resuelta (5 min) | 303 | 15 | 35 | 14 | 15 | 133 | 0 | 0 | 45 | 19 | 25 | — |
| httpx_3672 | no resuelta (5 min) | 294 | 0 | 34 | 4 | 16 | 107 | 8 | 0 | 84 | 28 | 13 | 291 |
| requests_7328 | resuelta | 208 | 1 | 14 | 8 | 15 | 61 | 17 | 14 | 26 | 20 | 33 | 159 |
| rich_3006 | resuelta | 157 | 3 | 26 | 6 | 0 | 21 | 14 | 15 | 28 | 26 | 18 | 121 |
| rich_3469 | no resuelta | 251 | 3 | 19 | 15 | 15 | 91 | 29 | 0 | 37 | 21 | 21 | 218 |
| rich_3521 | no resuelta (5 min) | 295 | 3 | 16 | 5 | 15 | 194 | 0 | 0 | 21 | 23 | 18 | — |
| **total** | 3/10 | 2.697 | 62 | 266 | 157 | 121 | **1.014** | 149 | 28 | 396 | 272 | 228 | |

Por tipo de llamada:

| tipo | llamadas | s | tokens/llamada |
|---|---|---|---|
| decisión de un sub-agente | 95 | 1.735 (72 %) | 662 |
| `open` de un sub-agente | 41 | 224 + 65 de script | 185 |
| respuesta final (copia una línea) | 51 | 172 | 117 |
| coordinador: despacho | 57 | 139 | 94 |
| coordinador: `route.py next` | 15 | 100 | 192 |
| coordinador: submit y frase final | 8 | 33 | 150 |

Sobrecoste por invocación de sub-agente (despacho + `open` + final): **600 s en 57 invocaciones**, entre 6 y 14 s cada una
(specify 14,2 s, locate 11,7 s, edit 10,3 s). Con las rutas y el cierre del coordinador, unos **73 s por tarea** son
mecánica.

Hechos que se desprenden de las trazas:

- **Specify se lleva el 42 % de toda la generación** (1.014 s en 41 decisiones). En 26 de las 41 la decisión agota el
  presupuesto de 1.024 y tarda unos 30 s. El tiempo de una decisión de specify lo fija el presupuesto: en V22, con 2.048,
  agotaron el presupuesto 8 de 9 decisiones, de 56 a 60 s cada una; en V23, con 1.024, tardan unos 30 s.
- **De esas 41 decisiones, 14 terminaron en «no change» (332 s)** y 8 de las 9 llamadas `missing` no llevaron a nada
  (220 s). Solo 15 escribieron una frase que se guardó.
- **Llamadas que no cambian nada** (rechazadas, «not used», malformadas o sin respuesta): **264 s**. Son rich_3521
  pasos 24-28 (135 s), fastapi_15800 pasos 22 y 24 (42 s), fastapi_14583 paso 22 (29 s), httpx pasos 7, 8 y 39 (57 s).
- **Scope agotó su presupuesto de 512 en sus 8 decisiones, y en 6 contestó «none».** Añadió 5 sitios, y ninguno llegó a
  editarse: los dos que recibieron veredicto fueron «no change». En V22: 7 «none» de 9, y 3 sitios añadidos sin ningún edit.
  En 20 ejecuciones, scope no aportó ningún cambio.
- **Las 4 tareas con el parche vacío agotaron el tiempo antes de su primer edit.** En las demás, el primer edit llegó
  entre los 121 y los 291 s. Cada segundo ahorrado antes de specify y edit se convierte en parche.
- V22 → V23: quitar reproduce (647 s en V22) y reducir a la mitad los presupuestos permitió pasar de 3 a 9 invocaciones de
  edit. Las respuestas finales bajaron de 333 a 117 tokens por llamada.

## 2. Propuestas, ordenadas por ahorro esperado por tarea

### P1. Especificar solo los sitios que alguien eligió (≈ 30 s por tarea)

**Qué desperdicio elimina.** Scope cuesta 121 s de decisiones y 62 s de sobrecoste en 8 tareas, es decir, 183 s, y sus
5 sitios añadidos no produjeron ningún edit. Los dos que llegaron a specify (los `HTTPConnection.__init__` gemelos de
httpx) costaron 43 s de decisiones «no change» y unos 13 s de invocación. Los sitios que el script añade por «same pattern»
(fastapi_14583: `is_bytes_or_nonable_bytes_annotation` e `is_uploadfile_or_nonable_uploadfile_annotation`) costaron
28,7 + 29,3 s, ambos «no change», más 9 s de invocación. Mientras tanto, en esa tarea el segundo edit quedó sin hacer en el
segundo 291. Total: unos 305 s en 10 tareas.

**Mecanismo.**
- La pregunta de scope («¿qué código relacionado debe cambiar también?») deja de ser una etapa y se integra en la vista de
  specify de cada sitio. Esa vista ya muestra «Code that calls X» y «Other members of its class», y specify ya acepta
  `['missing', nombre, qué]`.
- route.py deja de enrutar a scope.
- Los añadidos automáticos por **definición idéntica** (los gemelos ahttpx/httpx) siguen siendo sitios.
- Los añadidos por **«same pattern»** pasan a ser una línea de la vista de specify («Code with the same pattern, may need
  the same change: …») que el especificador puede convertir en sitio con `missing`, en lugar de sitios que cuestan una
  decisión cada uno.

No es quitar una capacidad, sino cablearla donde el modelo ya mira.

**Ahorro.** (183 + 56 + 67) s / 10 ≈ **30 s por tarea**. En fastapi_14583, por ejemplo, el edit de `v1.py` habría tenido
unos 70 s.

**Riesgo para la calidad.** Bajo o medio. Se pierde una pasada dedicada a buscar llamantes que también deben cambiar, pero
en 17 decisiones de scope eso no aportó ningún cambio. El coste es que una omisión real se paga ahora con un `missing`
(una decisión de unos 30 s) en lugar de con la etapa entera.

**Esfuerzo:** pequeño a medio (route.py, la vista de specify y la regla de añadidos).

**Cómo medirlo.** Con las mismas 10 tareas: segundos de scope + specify por tarea, número de veredictos «no change»,
momento del primer edit, y un recuento de los edits del parche final en funciones que el plan no tenía antes de specify.
Este recuento debería ser igual o mayor, no menor.

**Genérico:** sí. No depende de ningún repositorio y se basa en el origen de cada sitio, no en nombres.

### P2. Reparar el protocolo `missing` de specify (≈ 17 s por tarea, y un parche posible en rich_3521)

**Qué desperdicio elimina.** Hay cuatro fallos del autómata, todos vistos en las trazas:

- **(a) Reabrir un «no change».** En rich_3521, el paso 23 agota el presupuesto citando el código y contesta «no change»
  para `_split_cells`. En el paso 24, el modelo ve el error y manda `missing _split_cells <frase correcta>`. El script lo
  rechaza tres veces («already a place of the fix (its sentence: no change)») y la tarea termina con el parche vacío: pasos
  24-28 = **165 s**.
- **(b) `missing` sobre el propio sitio.** En fastapi_15800, paso 31, el especificador de `APIRouter.handle` manda
  `missing APIRouter.handle <qué cambia>`. El script lo convierte en un sitio nuevo y luego otra decisión dice «no change»
  (29,4 s). La frase real se pierde.
- **(c) Una ruta de fichero en `missing`.** En fastapi_14583, paso 22: `missing fastapi/_compat/shared.py` acaba en «No
  code named …» (29,4 s), aunque locate sí acepta «path.py top level».
- **(d) Vuelta al coordinador tras un `missing`.** En fastapi_14851, la invocación termina y el coordinador lanza otra
  para el mismo `APIRouter.on_event` (despacho de 6,7 s, `open` de 4,5 s y final de 2,1 s). En fastapi_14986, el
  coordinador se confunde y llama dos veces a route.py (13,5 s), y luego reabre `get_openapi` (4 s) y responde (2,5 s).

**Mecanismo.**
- (a) Un `missing` que nombra un sitio con veredicto «no change» lo reabre: se especifica a continuación y después vuelve
  el sitio actual.
- (b) Un `missing` que nombra el sitio actual se toma como su frase.
- (c) Una ruta de fichero se resuelve a su nivel superior, como en locate.
- (d) Tras especificar el código que faltaba, el sitio original continúa en la misma invocación («Your part goes on
  with…»).

**Ahorro.**
- rich_3521: 165 s de bucle frente a 2 decisiones de unos 30 s = 105 s.
- fastapi_15800: 29 s.
- fastapi_14583: 29 s.
- fastapi_14851: 13 s.
- fastapi_14986: 23 s.

En total unos 170 s / 10 ≈ **17 s por tarea**. Además, rich_3521 tendría su frase hacia el segundo 170 en lugar de
ninguna.

**Riesgo para la calidad.** Bajo. Solo deja al modelo corregir lo que ya intentó corregir. (b) y (c) convierten en efecto
respuestas que ya traían el contenido. No se aceptan a ciegas las frases de `missing` sobre otro código: en 3 de 5 casos
(OpenAPI, Server, `APIRouter.handle`), el especificador de ese código las rechazó después.

**Esfuerzo:** pequeño (reglas del autómata en specify.py y route.py).

**Cómo medirlo.** Contar las llamadas con respuesta «already a place», «No code named», «not used» o «too long» (hoy 7, con
205 s) y las invocaciones de specify por tarea (hoy 18 en 10 tareas). Repetir rich_3521, fastapi_14986 y fastapi_15800.

**Genérico:** sí. Son reglas del protocolo, independientes del código.

### P3. Aplicar en el script las frases literales y propagarlas a los gemelos (≈ 13 s por tarea, y patches en tareas que se cortan)

**Qué desperdicio elimina.** En 6 de las 15 frases guardadas, el especificador ya escribió el cambio exacto, con la forma
«replace \`A\` with \`B\`»: rich_3006, requests_7328, rich_3469 `Text.align`, los dos `__repr__` de httpx y
fastapi_14986 `get_openapi`. En los 4 casos que llegaron a edit, el editor aplicó exactamente ese reemplazo y su decisión
encontró la respuesta en el primer 3-5 % del pensamiento. Aun así, cada invocación de edit costó:

| tarea | despacho | `open` | decisión | final | total |
|---|---|---|---|---|---|
| rich_3006 | 4,2 | 6,8 | 15,2 | 1,7 | 27,9 s |
| requests_7328 | 1,5 | 6,9 | 17,6 | 3,0 | 29,0 s |
| rich_3469 | — | — | — | — | 20,5 s |
| httpx | — | — | — | — | 23,3 s |

Son 101 s en total. Además, en httpx los dos `HTTPParser` gemelos (ahttpx/httpx, con la misma definición) se
especificaron por separado: la segunda vez dieron las mismas frases, con 18 s de decisiones y 10 s de invocación. En
fastapi_14986, la frase literal llegó en el segundo 298 y no hubo tiempo de llamar al editor: el parche quedó vacío.

**Mecanismo.** Cuando la frase de un sitio contiene un único «replace \`A\` with \`B\`» y A aparece exactamente una vez en
el código del sitio (comparando sin el sangrado; `\n` se lee como salto de línea), specify.py aplica el reemplazo con el
sangrado del original. Después comprueba la sintaxis y sigue con los mismos chequeos que edit. Si algo falla (A ausente o
repetido, error de sintaxis), el sitio va al editor como hoy. Un sitio de definición idéntica recibe la frase y el mismo
reemplazo, con las mismas comprobaciones.

**Ahorro.** 101 s de editores + 28 s del gemelo = 129 s / 10 ≈ **13 s por tarea**. Además, en dos tareas que se cortaron
(fastapi_14986 y el segundo `__repr__` de httpx) el cambio habría quedado en el parche.

**Riesgo para la calidad.** Bajo. Se aplica lo que el modelo ya decidió, sin interpretarlo, y en los 4 casos observados el
editor no añadió nada. Si el editor corrige a veces una frase literal mala, eso se pierde, pero las comprobaciones de
sintaxis y tests siguen ahí.

**Esfuerzo:** pequeño a medio.

**Cómo medirlo.** Contar los sitios aplicados por el script, los que caen al editor y los que el chequeo deshace. Comparar
el diff con el de un editor en las mismas frases (repetir rich_3006, requests_7328 y rich_3469) y medir el momento del
primer edit.

**Genérico:** sí. Se basa en la forma de la frase y en la igualdad textual del código.

### P4. Quitar la deliberación del coordinador (≈ 12 s por tarea)

**Qué desperdicio elimina.**
- La primera llamada de cada tarea es siempre `route.py next`, y su respuesta siempre es «call requirements with 'the
  issue'»: **63 s** en 10 tareas (6,3 s cada una).
- 8 llamadas del coordinador con 200-430 tokens de pensamiento (64 s, frente a unos 1,5 s de una normal). En todas, el
  coordinador razona sobre la parte «lo que hice» de la línea del sub-agente, que le sorprende:
  - «Saved for APIRouter.__init__… call specify with 'APIRouter.on_event'» otra vez;
  - «R2 is decided» cuando pidió R1;
  - «Saved for APIRouter.frontend» cuando pidió `FastAPI.frontend`;
  - submit sin ningún edit.
- Tras el submit, la «frase sobre el cambio» y el propio submit con dudas costaron 26 s, 20 de ellos en fastapi_14851, que
  terminó en el segundo 302.

**Mecanismo.**
- El prompt del coordinador da directamente la primera llamada, `requirements` con «the issue». route.py sigue siendo la
  vuelta para todo lo demás.
- La línea que el sub-agente devuelve contiene solo «Next: call the tool X with request 'Y'». El resumen de lo hecho va al
  estado del script, no al coordinador.
- Tras `submit_patch`, la respuesta de cierre es un texto fijo («Patch submitted.»).

**Ahorro.** 63 + unos 45 + unos 18 ≈ 126 s / 10 ≈ **12 s por tarea**.

**Riesgo para la calidad:** muy bajo. El coordinador no decide nada del issue.

**Esfuerzo:** pequeño (prompt y textos de los scripts).

**Cómo medirlo.** Segundos del coordinador por tarea (hoy 27) y número de llamadas del coordinador con más de 200 tokens
(hoy 11).

**Genérico:** sí.

### P5. Requirements en una sola decisión (≈ 8-14 s por tarea)

**Qué desperdicio elimina.** La primera llamada de requirements es una consulta de nombres: el modelo manda los nombres del
issue y `req.py` dice qué define el repositorio. Costó 91 s en las 9 tareas que la hicieron. En fastapi_14448, 29 s de
pensamiento ya redactaban los requisitos, que luego se rehicieron en otros 33 s. El protocolo de dos fases, distinguidas
solo por el orden de las llamadas, falló en httpx: la segunda consulta («Server») se guardó como requisito «R1: Server», y
luego vinieron otra llamada «not used» y una respuesta sin línea, con 54 s perdidos y el requisito destruido.

**Mecanismo.** El script extrae él mismo los nombres del enunciado (texto entre comillas invertidas, nombres con punto y
nombres que existen en el índice del repositorio) y muestra sus definiciones. O bien en un `open` determinista, o bien
dentro de la respuesta a los requisitos, pidiendo confirmación solo si algún nombre citado no existe. El modelo solo manda
pares.

**Ahorro.** Con `open`: 91 − 9 × 7 + 54 ≈ 82 s, es decir, **8 s por tarea**. Con las definiciones en la respuesta: hasta
145 s, es decir, **14 s por tarea**.

**Riesgo:** bajo. La información que recibe el modelo es la misma.

**Esfuerzo:** pequeño.

**Cómo medirlo.** Llamadas y segundos de requirements por tarea, y requisitos guardados que no son una frase del issue.

**Genérico:** sí.

**Si se suman** (no son del todo aditivas): unos 80 s por tarea de los unos 270 que hoy se usan. Las 4 tareas con el parche
vacío necesitaban entre 30 y 120 s más para llegar a su primer edit.

## 3. Una apuesta mayor que las trazas no pueden demostrar

**Especificar en paralelo.** El brief dice que vLLM atiende una sola petición a la vez y usa el 2 % de la caché KV: no se
está aprovechando el batching de decodificación. Las decisiones de specify sobre sitios distintos de un mismo requisito
son independientes salvo por la línea «other places». Con un `ParallelAgent` de 2-3 especificadores (cada uno toma un
hueco de la cola; si un hueco está vacío, responde enseguida), envuelto como agent_tool, vLLM los decodificaría juntos.

Si cada flujo mantuviera unos 30 tok/s con 3 en paralelo, specify (1.014 s) bajaría entre un 35 y un 45 %: unos **35-45 s
por tarea**. Ese ritmo con batch en 4×L4 TP4 está **sin medir**: hay que medirlo antes, con dos o tres peticiones
simultáneas.

Riesgos:
- Se pierde la vista de las frases de los otros sitios. En requests_7328, el «no change» de `Session.send` se apoyó en
  ella.
- Los `missing` son secuenciales.
- Las escrituras paralelas en un mismo fichero exigirían un bloqueo, así que no conviene paralelizar edit.

Esfuerzo: grande.

## 4. Ideas consideradas y descartadas

- **Bajar el presupuesto de specify** (1.024 → 512-768). Ahorraría unos 7-15 s por decisión, porque el tiempo lo fija el
  presupuesto. Pero la respuesta aparece entre el 30 y el 85 % del pensamiento en las decisiones medidas. Además, en
  rich_3521 un «no change» cortado por el presupuesto fue erróneo (en V22, con 2.048, también). Prefiero menos decisiones
  (P1-P3) a decisiones más cortas.
- **Bajar el presupuesto del coordinador.** Su media ya es de 118 tokens. Las llamadas largas se deben a líneas que lo
  confunden; P4 ataca esa causa.
- **Pasar la vista en la petición para ahorrar el `open`** (224 s). El coordinador tendría que generar entre 600 y 3.500
  tokens (de 17 a 100 s) en lugar de unos 185.
- **Cambiar coordinador + agent_tool por SequentialAgent o LoopAgent.** Se ahorraría el despacho (139 s), pero el
  historial se comparte (la semántica de `include_contents` en bucles está sin comprobar), no hay enrutado condicional y
  el esfuerzo es grande.
- **Una sola decisión de specify para todos los sitios.** Si el 63 % de las decisiones de un solo sitio ya agotan 1.024,
  una decisión multi-sitio necesitaría unos 2.048 (57 s) y solo ganaría con 3 sitios o más.
- **Editar todos los sitios en una sola invocación.** Solo hubo 3 sitios adicionales de edit, unos 10 s cada uno: 3 s por
  tarea, con el riesgo de que el contexto crezca.
- **Quitar review.** Solo hubo 2 invocaciones (45 s), y es la única comprobación con tests.
- **Acelerar los scripts.** Suman 228 s (8 %); la parte mayor es un test de 24 s en el `open` de review.
- **El arranque del harness** (0-15 s, 62 s en total) no depende de nosotros.
- **Pedir que no se copie código en el pensamiento.** El 14 % de los caracteres del pensamiento de specify son líneas
  copiadas literalmente de la vista (en edit, el 43 %, pero ahí son borradores del código nuevo). Se ganaría como mucho
  unos 4 s por decisión, con efecto incierto.

## 5. Cómo medir cualquiera de estas propuestas

Usar las mismas 10 tareas y la misma contabilidad. Por llamada: duración = `elapsed_s` − final del evento anterior. Por
tarea, registrar:
- segundos por etapa, separando decisiones y mecánica;
- decisiones que agotan el presupuesto;
- veredictos «no change»;
- llamadas rechazadas;
- momento del primer edit;
- parches no vacíos y tareas resueltas.

Con temperatura 1,0 y n = 10, las diferencias de menos de unos 20 s por tarea son ruido. Conviene comparar los segundos por
etapa, más estables que el número de tareas resueltas.
