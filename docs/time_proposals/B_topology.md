# B. Topología de orquestación: propuestas para acelerar

Enfoque: cuántos viajes al modelo necesita una tarea y cómo se componen (secuencial o paralelo). Las cifras salen todas del brief: 10 tareas, 2.635 s de modelo, unos 263 s por tarea, y 7 de 10 tareas cortadas a los 300 s.

## Punto de partida: lo que cuesta un salto

Cada vez que el trabajo pasa a otro subagente se pagan tres llamadas que no deciden nada:

| Llamada | Total | Llamadas | Por llamada |
|---|---|---|---|
| Coordinador (reenvía la línea) | 276 s | 80 | 3,45 s |
| `open` del subagente | 227 s | 41 | 5,5 s |
| Respuesta final (copia una línea) | 235 s | 54 | 4,35 s |

- **Un salto cuesta unos 13,3 s.** Hay unas 5,4 invocaciones de subagente por tarea (54 respuestas finales en 10 tareas).
- **El transporte suma 738 s, el 28 % del tiempo de modelo**, unos 74 s por tarea. Es el techo de lo que se ahorra quitando saltos.
- **El otro gran bloque es especificar: 124 s por tarea (47 %).** Sus decisiones se hacen una tras otra, aunque los lugares son independientes. Cada decisión gasta todo el presupuesto de pensamiento, unos 25–30 s.
- **La GPU está casi vacía.** vLLM atiende una petición cada vez, con el 2 % de la caché KV en uso. Con TP4 sobre 4×L4 y pesos W4A16, decodificar con lote 1 está limitado por la lectura de pesos y por los all-reduce entre GPUs. Generar 2–4 secuencias a la vez cuesta casi lo mismo por paso que generar una (se espera una pérdida del 10–30 % por secuencia). **El paralelismo es la palanca más grande que queda sin usar.**

Condición previa para la propuesta 1: confirmar que el vLLM del harness no limita `max_num_seqs` a 1. El brief dice que «sirve una petición a la vez», y eso puede ser el efecto de un agente secuencial y no un límite del servidor. Cómo comprobarlo: en el registro del arranque de vLLM del harness, y en una corrida de Kaggle donde dos peticiones se solapen en el tiempo (las marcas de inicio y fin de las llamadas lo muestran).

---

## 1. Abanico paralelo por lugar: especificar y editar en ranuras simultáneas

**Técnica:** fan-out / map en paralelo (ParallelAgent), con la parte crítica (aplicar la edición y correr los tests) serializada en el script.

**Mecanismo**
- Se añade un subagente `places` que el coordinador llama como `agent_tool`. `AgentTool` acepta cualquier `BaseAgent`, y el resultado es el texto del último evento con contenido.
- `places` es un `ParallelAgent` con K ranuras, por ejemplo K = 4. Cada ranura es un `SequentialAgent [specify_slot_i, edit_slot_i]`, o un solo `LlmAgent` encadenado (ver la propuesta 2).
- El `open` de la ranura i le pide al script los lugares asignados a i:
  - Grupo: un lugar y sus dependientes directos (llamador/llamado del plan, o la misma requisito con cambio de firma) van en la misma ranura, en serie. Los grupos independientes van en paralelo.
  - Ranura vacía: el script contesta «no place» con la línea final, lo que cuesta un `open` y una respuesta, pero en paralelo.
  - Más grupos que ranuras: una ranura procesa varios, uno tras otro.
- Cada ranura escribe su frase y luego hace su edición.
- El script toma un `fcntl.flock` sobre un fichero de estado alrededor de tres pasos: aplicar, comprobar la sintaxis y correr los tests relacionados. Las decisiones del modelo corren en paralelo y las escrituras en `/workspace` van en serie.
- La forma por rango de líneas se resuelve por el nombre de la definición en el momento de aplicar, no por las líneas absolutas de la vista, porque otra ranura puede haber desplazado el fichero. La forma de definición entera ya es robusta a eso.
- **Todas las ranuras responden la misma línea de "next call"**, que el script conoce de antemano (tras los lugares viene la revisión o el envío). Así no importa qué ranura termine la última.
- Variante sin ParallelAgent: el script le pide al coordinador N llamadas a `edit_place` en una sola respuesta, y el ADK reciente ejecuta en paralelo las llamadas a herramientas de una misma respuesta. Es menos determinista, porque depende de que el modelo emita N llamadas. Queda como plan B.

**Ahorro esperado**
- Hoy especificar cuesta unos 124 s por tarea: unos 100 s de decisiones (unas 4 decisiones de unos 25 s) más unos 20 s de `open` y respuesta.
- Con K = 4 y una penalización de lote de ×1,25 por secuencia, las decisiones tardan unos 25 × 1,25 ≈ 31 s de pared, más un `open` y una respuesta en paralelo (unos 10 s). En total, unos 41 s.
- Las ediciones (unos 23 s por tarea más sus saltos) se solapan con las especificaciones de las otras ranuras: de 5 a 10 s más.
- **Total: de 70 a 90 s por tarea** si vLLM agrupa las peticiones; **0 s si `max_num_seqs` = 1**. Además, varias de las 7 tareas cortadas a los 300 s llegarían a editar y enviar, que es donde está la puntuación.

**Riesgo para la calidad**
- **Moderado.** Un especificador ya no ve en su contexto las frases que escribió para otros lugares, como hace hoy al tratar varios lugares en una sola llamada. Se pierde coherencia cuando un lugar depende de otro, por ejemplo un parámetro nuevo que el llamador debe pasar.
- Mitigaciones:
  - agrupar en la misma ranura los lugares con relación llamador/llamado o de cambio de firma;
  - mostrar en cada vista los nombres de los otros lugares y la requisito que cubre cada uno.
- Ediciones simultáneas: el bloqueo del script evita que los tests de una ranura vean a medias la edición de otra.

**Esfuerzo:** medio. Hacen falta la configuración YAML (ParallelAgent dentro de un agent_tool), el reparto de grupos y el bloqueo en el script, y resolver las ediciones por nombre al aplicarlas.

**Cómo medirlo**
- Sobre las mismas 10 tareas en Kaggle:
  - el tiempo de pared desde el primer `open` de especificar hasta el último resultado de editar;
  - el solapamiento real de peticiones (los intervalos de las llamadas se cruzan);
  - los tokens/s por petición con 1, 2 y 4 a la vez;
  - el número de tareas que llegan a `submit_patch` antes de los 300 s, y las resueltas.
- Primero, un microbenchmark de 1/2/4 peticiones simultáneas en el mismo vLLM de Kaggle. El vLLM de Colab (una sola GPU Blackwell) no reproduce el escalado de TP4 sobre L4.

**¿Genérico?** Sí. La paralelización depende solo de la estructura del plan (lugares y sus relaciones), no del repositorio ni del issue.

---

## 2. Encadenar etapas contiguas dentro de una misma invocación

**Técnica:** fusionar pasos cuya única frontera es el transporte (*hop elimination*). Cuando la etapa siguiente usa el mismo contexto, el script devuelve directamente su vista en lugar de «responde X».

**Mecanismo:** tres cadenas, cada una dentro de un solo subagente.
- **Localizar → acotar.** Tras la elección de candidatos, el script contesta con la lista de código relacionado y la pregunta de acotar. Las dos etapas tienen hoy el mismo presupuesto (512), así que no cambia nada de la configuración.
- **Especificar → editar del mismo lugar.** Tras la frase, el script contesta con la ventana de edición de ese lugar. El editor ve el mismo código que acaba de leer el especificador (el brief dice que especificar ve el código del lugar y lo que llama) y su propio razonamiento. Las dos tienen 1.024 de presupuesto; se sube el tope de salida a 4.096. Es la unidad natural de cada ranura de la propuesta 1.
- **Revisar → enviar.** Si la revisión no se salta, el subagente de revisión recibe al final la línea de envío. Esta cadena ahorra poco, porque la revisión se salta a menudo.
- Lo que no se encadena: requisitos con localizar. El presupuesto es por agente, y localizar pasaría de 512 a 1.024, con riesgo de pensar el doble en una decisión de elegir de una lista.

**Ahorro esperado**
- Cada fusión ahorra un salto (coordinador + `open` + respuesta ≈ 13,3 s).
- Localizar → acotar: acotar corre en unas 6 de cada 10 tareas (17 llamadas, unas 3 por invocación), así que ahorra unos 0,6 × 13,3 ≈ 8 s.
- Especificar → editar:
  - sin la propuesta 1: hoy son 1 invocación de especificar + N de editar; encadenado son N, así que se ahorra al menos 1 salto por tarea que llega a editar (unos 13 s);
  - con la propuesta 1: la ranura ya es especificar+editar y el ahorro está incluido allí.
- **Total: de 15 a 25 s por tarea.**

**Riesgo para la calidad:** bajo.
- El contexto crece solo con la vista de la etapa siguiente, de unos 1–3 mil tokens, lejos de la compactación a 14.336.
- El editor conserva el porqué del cambio que razonó al especificar. Es más probable que eso ayude a que estorbe.

**Esfuerzo:** pequeño. Son cambios en la máquina de estados del script (qué etapa sigue en la misma invocación) y en las instrucciones de los dos agentes fusionados.

**Cómo medirlo:** en las trazas, contar llamadas de coordinador, `open` y respuesta final por tarea (hoy 8 / 4,1 / 5,4). Mirar también los tokens de pensamiento de acotar y de editar cuando están encadenados, frente a cuando no lo están. Y comparar la tasa de ediciones deshechas, para ver si la calidad se mantiene.

**¿Genérico?** Sí. Solo cambia el orden de entrega de las vistas.

---

## 3. Abaratar o quitar el coordinador

**Técnica:** que el enrutamiento lo haga la composición del ADK, sin LLM. Si no se puede, un enrutador sin pensamiento.

**Mecanismo, variante A (pequeña)**
- El coordinador solo copia una línea a una llamada: `thinking_budget` mínimo (0 si el harness lo acepta como «sin pensamiento», o 32–64) y `max_output_tokens` de unos 512.
- Hoy gasta 118 tokens por llamada, casi todo pensamiento.

**Mecanismo, variante B (grande)**
- El agente raíz pasa a ser `SequentialAgent [front, places, finisher]`:
  - `front` es un `LlmAgent` con requisitos y localizar→acotar encadenados;
  - `places` es el ParallelAgent de la propuesta 1;
  - `finisher` es un `LlmAgent` con revisión y `submit_patch`.
- Cada `LlmAgent` lleva `include_contents: none`, así que solo ve su instrucción, la respuesta del agente anterior y sus propias llamadas. Es el «contexto fresco» de los agent_tool, sin coordinador.
- El "back" (volver a localizar) se sustituye por lo que ya permite el paso de terminar: una edición más dentro del `finisher`, con sus propios lugares.
- Requisito: comprobar que el harness acepta como raíz un agente no-LLM con `submit_patch` declarado en un hijo. Hoy el mensaje de la tarea lista las herramientas declaradas y el enunciado va en la instrucción del raíz.

**Ahorro esperado**
- A: de 118 a unos 40 tokens por llamada, que son unos 2,2 s menos × 8 llamadas por tarea ≈ **15–18 s por tarea**.
- B: desaparecen las 80 llamadas de coordinador (276 s / 10 = **27,6 s por tarea**). Las respuestas finales ya no tienen que copiar una línea exacta: pueden ser «ok», con menos pensamiento, quizá 1–2 s menos cada una. Total, unos **30–35 s por tarea**.

**Riesgo para la calidad**
- A: bajo-moderado. Sin pensamiento, el 31B puede equivocarse más al copiar argumentos. Hay que vigilar la tasa de llamadas malformadas del coordinador.
- B: moderado. Se pierde el enrutamiento dinámico. Cualquier etapa que el script quiera saltar dentro de la secuencia fija cuesta un `open` y una respuesta (unos 10 s). Por eso la revisión va dentro del `finisher` y no como agente propio, y la reproducción, que hoy se salta, se quita. Además se depende de la semántica de `include_contents: none` y de la raíz no-LLM en el harness.

**Esfuerzo:** A pequeño; B grande.

**Cómo medirlo**
- A: tokens y segundos por llamada del coordinador, y llamadas rechazadas o malformadas.
- B: segundos totales de enrutamiento más respuestas finales por tarea, y tareas que llegan a `submit_patch`.

**¿Genérico?** Sí, las dos variantes.

---

## 4. Requisitos y localizar en paralelo

**Técnica:** ejecutar en paralelo dos etapas que solo dependen débilmente una de otra.

**Mecanismo**
- `ParallelAgent [requirements, locate]` como un solo agent_tool, o como primer paso de la raíz en la variante 3B.
- Localizar elige candidatos a partir del enunciado y de sus propios términos de búsqueda, sin la lista de requisitos.
- Cuando terminan los dos, el script asigna los lugares elegidos a cada requisito por solapamiento léxico con los nombres y con las líneas que coinciden.
- Lo que el mapa deje sin cubrir se muestra en acotar, que sigue siendo secuencial, como «requisito sin lugar: elige o añade».
- Las dos ramas escriben en partes distintas del estado JSON, con el bloqueo del script.

**Ahorro esperado**
- Requisitos cuesta unos 34 s por tarea y localizar unos 27 s.
- En paralelo, el tiempo de pared es el máximo × 1,2 ≈ 41 s, frente a 61 s.
- **Unos 20 s por tarea**, siempre que vLLM agrupe las peticiones.

**Riesgo para la calidad:** moderado.
- Hoy localizar elige por requisito, y eso enfoca la elección. Sin la lista, puede elegir por la superficie del enunciado.
- Lo compensa en parte que acotar vea los huecos de cobertura.
- Va contra un ítem probable de la checklist: descomponer antes de buscar ayuda al modelo. Habría que medirlo.

**Esfuerzo:** medio.

**Cómo medirlo:** con `tools/bench_locate.py` (las 129 tareas), comparar la precisión de localización con requisitos y sin ellos antes de llevarlo al modelo. Después, el tiempo de pared de la fase inicial y la cobertura de requisitos al enviar.

**¿Genérico?** Sí.

---

## 5. Un lugar por grupo de copias idénticas

**Técnica:** deduplicar trabajo antes del fan-out, para que haya menos viajes en lugar de viajes más rápidos.

**Mecanismo**
- Al planificar, el script agrupa las definiciones con el mismo nombre cualificado relativo y el mismo AST normalizado: el mismo código copiado en dos paquetes, o vendorizado.
- El modelo especifica y edita una sola vez el representante del grupo. El script aplica la misma definición nueva a cada copia, comprueba la sintaxis y corre los tests de cada una.
- Si una copia falla, se deshace solo esa copia y queda como lugar propio, que se especifica y edita aparte.
- Los gemelos async/sync no son idénticos y se tratan como hoy.
- Además, los lugares que un especificador añade «a petición» van a la ranura de su lugar de origen (propuesta 1) y no a la cola global.

**Ahorro esperado**
- Por copia evitada: una decisión de especificar (unos 25 s) + un salto de editar (13,3 s) + una decisión de editar (unos 8–10 s) ≈ 45–50 s.
- El brief dice que estos casos «a menudo» multiplican las llamadas. Si una de cada 3–4 tareas tiene una copia, salen **unos 12–16 s por tarea de media**, y mucho más en esas tareas.
- Con la propuesta 1 el ahorro de pared es menor, porque la copia iría en otra ranura en paralelo. Aun así libera ranuras y evita que las dos copias acaben con ediciones distintas.

**Riesgo para la calidad:** bajo. La identidad del AST es una prueba fuerte de que el cambio debe ser el mismo. Si los tests de una copia rompen, se deshace por separado.

**Esfuerzo:** pequeño-medio.

**Cómo medirlo:** número de lugares, de decisiones de especificar y de decisiones de editar por tarea, frente a los de la corrida anterior. Ver también si los parches tocan las dos copias con el mismo cambio.

**¿Genérico?** Sí. La igualdad estructural no depende de nombres concretos ni del repositorio.

---

## Orden y combinación

| # | Propuesta | Ahorro por tarea | Riesgo | Esfuerzo |
|---|---|---|---|---|
| 1 | Abanico paralelo por lugar | 70–90 s (0 s si vLLM no agrupa) | moderado | medio |
| 3 | Coordinador sin LLM (B) o sin pensamiento (A) | 30–35 s (B) / 15–18 s (A) | moderado / bajo | grande / pequeño |
| 2 | Encadenar etapas contiguas | 15–25 s | bajo | pequeño |
| 4 | Requisitos ‖ localizar | unos 20 s | moderado | medio |
| 5 | Deduplicar copias | 12–16 s de media | bajo | pequeño-medio |

- Los ahorros no se suman del todo: la 2 y la 5 se solapan con la 1.
- Combinación razonable: 1 + 2 + 3A + 5 baja una tarea típica de unos 263 s a 150–170 s de modelo, si vLLM agrupa.
- Primer paso barato y sin dependencias: 2 + 3A, unos 30–40 s.
- Antes de invertir en la 1, medir el escalado de vLLM con peticiones simultáneas en Kaggle.

## Ideas consideradas y descartadas

- **`skip_summarization` en los agent_tool, para no pagar la respuesta del coordinador.** Cierra el turno del coordinador al volver el subagente. Entonces el harness lo empuja con nudges, que son más llamadas, o la tarea acaba sin enviar.
- **Quitar el `open` inyectando la vista por estado** (`AgentTool` copia el estado del padre al hijo y la instrucción admite `{clave}`). Un script de skill no puede escribir en el estado de la sesión; solo pueden hacerlo los callbacks, `output_key` o las herramientas con `tool_context`. Poner la vista en el `request` del coordinador obligaría al modelo a generarla, que es lo más caro que hay.
- **Un solo agente con todas las etapas en un mismo contexto.** Quitaría los 74 s por tarea de transporte, pero el historial crecería hasta compactarse a 14.336 tokens y se volvería a lo que llevó a las microtareas: el editor leyendo en lugar de decidir.
- **`LoopAgent` sobre editar con N iteraciones fijas.** Sin salida temprana, cada iteración vacía cuesta un `open` y una respuesta (unos 10 s). El reparto por ranuras de la propuesta 1 consigue lo mismo sin iteraciones muertas.
- **Ediciones en paralelo sin serializar la aplicación.** `/workspace` es compartido: los tests de una rama verían la edición a medias de otra y deshacerían ediciones buenas.
- **Secuencias de parada para cortar la respuesta-copia.** En vLLM las cadenas de parada actúan también sobre el texto de razonamiento. Es frágil y podría cortar la llamada a herramienta.
- **Ejecución especulativa** (especificar los mejores candidatos mientras localizar decide). Sin código Python propio no hay forma de lanzar llamadas al modelo fuera de los agentes, y con ParallelAgent se gastaría lote en candidatos que luego se descartan.
- **Que el coordinador emita varias llamadas en paralelo como mecanismo principal.** Depende de que el 31B produzca N llamadas exactas en una respuesta. El ParallelAgent es determinista; esto queda solo como plan B de la propuesta 1.
