# Propuestas E: lo que muestran la literatura y los sistemas públicos

Punto de partida (del brief): 263,5 s de llamadas al modelo por tarea (2.635 s / 10), el 90 % es generación a unos
36 tok/s y el 85 % de lo generado es pensamiento. Por tarea:

| Partida | s/tarea |
|---|---|
| Decisiones de sub-agentes | 182,2 |
| Coordinador (enrutar) | 27,6 |
| Respuesta final de cada sub-agente (copia una línea) | 23,5 |
| `open` de cada sub-agente | 22,7 |
| Rechazadas o con error | 7,4 |

Specify solo suma 124 s por tarea (47 %). Una decisión difícil agota su presupuesto: 1.024 tokens / 36 tok/s ≈ 28 s.

Cada invocación de un sub-agente cuesta unos 13,4 s fijos antes de decidir nada: el coordinador (3,5 s), el `open`
(5,5 s) y la respuesta final (4,4 s). Con unas 5,4 invocaciones por tarea son unos 72 s (28 %).

Las propuestas están ordenadas por el ahorro esperado por tarea. Los ahorros no se suman sin más: la 1 y la 3 se
combinan bien entre sí, y la 2 y la 4 atacan el mismo pensamiento.

---

## 1. Decisiones independientes en paralelo (fan-out por lugar) aprovechando el batching de vLLM

**Técnica y fuentes**
- Ejecutar en paralelo las llamadas que no dependen entre sí:
  - LLMCompiler (Kim et al., 2023/ICML 2024): hasta 3,7× menos latencia que ReAct, <https://arxiv.org/abs/2312.04511>.
  - Skeleton-of-Thought (Ning et al., 2023/ICLR 2024): partes independientes de una respuesta decodificadas a la vez,
    hasta 2,39× más rápido según la v2, <https://arxiv.org/abs/2307.15337>.
- Por qué el paralelo sale casi gratis en este hardware:
  - vLLM agrupa las peticiones simultáneas por lotes continuos (PagedAttention, Kwon et al., 2023,
    <https://arxiv.org/abs/2309.06180>).
  - Con pesos de 4 bits, la decodificación está limitada por la memoria. Los kernels Marlin (los que usa vLLM para
    W4A16) mantienen una aceleración casi ideal hasta lotes de 16–32 (Frantar et al., 2024/PPoPP 2025,
    <https://arxiv.org/abs/2408.11743>). Decodificar 3–4 secuencias a la vez cuesta poco más que decodificar una.
- Hoy se desperdicia ese margen: el brief mide «una petición a la vez» con el 2 % de la caché KV en uso.

**Mecanismo**
- Specify procesa hoy varios lugares uno tras otro dentro de una misma llamada de agente. Cada lugar es una decisión
  aislada: ve su código y escribe una frase.
- Se sustituye por un `ParallelAgent` con K ranuras fijas, por ejemplo K = 4: `specify_1` … `specify_4`. Las ranuras
  son el mismo `LlmAgent` con una instrucción que solo cambia el número de ranura.
- Cada ranura hace `open <k>`, y el script le da el k-ésimo lugar pendiente. Una ranura sin lugar recibe «nada que
  hacer, responde: ok».
- Si hay más lugares que ranuras, o un especificador pide añadir lugares, el script lanza una segunda tanda.
- La configuración es declarativa: un `ParallelAgent`, llamado como `agent_tool` o como un paso de la secuencia de la
  propuesta 3.
- El mismo patrón sirve para locate y review cuando hay varios requisitos.
- En el script: el estado JSON necesita un bloqueo (`fcntl.flock`) o un fichero de salida por ranura que el paso
  siguiente fusiona. Las ranuras solo leen el código; las ediciones siguen en serie, porque comparten `/workspace` y
  las pruebas.

**Ahorro esperado**
- Con unos 3,5 lugares de media (el brief dice de 2 a 6), specify en serie tarda
  5,5 s + 3,5 × 28 s + 4,4 s ≈ 108 s por invocación.
- En paralelo, la ranura más lenta tarda (5,5 + 28 + 4,4) × f. El factor f es la ralentización por token con un lote
  de 4, que estimo en 1,1–1,3: la decodificación está limitada por memoria, y el all-reduce de TP4 por PCIe no crece
  con el lote. Resultado: unos 42–49 s.
- **Ahorro: unos 55–65 s por tarea en specify**, más unos 5–10 s si locate y review también se reparten.
- Un plan de un solo lugar no gana nada; uno de 6 lugares gana más de 100 s.

**Riesgo para la calidad**
- Bajo. Cada decisión ve exactamente el mismo contexto que ahora.
- Se pierde lo que un lugar «aprende» del anterior dentro de la misma sesión. Si esa coherencia importa (por ejemplo,
  la misma convención de nombres en dos sitios), el script puede pasar la frase del lugar principal a las ranuras de
  la segunda tanda.

**Esfuerzo:** medio. La configuración y el bloqueo en el script son sencillos. Hay que verificar dos cosas antes de
nada:
- que el ADK del arnés admite un `ParallelAgent` envuelto como `agent_tool`, o, si no, dentro de una secuencia;
- que el vLLM del arnés no tiene `--max-num-seqs 1`. Con la caché KV al 2 % parece que no.

**Cómo medirlo**
- En una ejecución de Kaggle o en Colab, comparar el tiempo de pared de la etapa specify por tarea (desde el primer
  `open` hasta la última respuesta) con el de la ejecución anterior.
- Registrar en el log de vLLM las peticiones en curso («Running: N reqs») y los tok/s por petición con N = 1 frente a
  N = 3–4. Ese dato da f directamente.
- Antes, una prueba aislada: 4 peticiones simultáneas de 1.024 tokens al vLLM de Colab frente a 4 seguidas.

**Genérico:** sí. Depende solo del número de lugares del plan, no del repositorio ni de la incidencia.

---

## 2. Presupuesto de pensamiento según la dificultad de cada decisión (decidido por el script)

**Técnica y fuentes**
- Pensar más no es monótonamente mejor:
  - En SWE-bench Verified, más «sobrepensamiento» va con menos resolución. Elegir la solución que menos sobrepiensa
    mejora el rendimiento casi un 30 % con un 43 % menos de coste. Cuadron et al., 2025, «The Danger of
    Overthinking», <https://arxiv.org/abs/2502.08235>.
  - La precisión sube y luego baja al alargar el pensamiento. Ghosal et al., 2025/NeurIPS 2025, «Does Thinking More
    Always Help?», <https://arxiv.org/abs/2506.04210>.
  - Hay tareas donde razonar más empeora el resultado por distracción con información irrelevante. Gema et al., 2025,
    «Inverse Scaling in Test-Time Compute», <https://arxiv.org/abs/2507.14417>.
  - Para una misma pregunta, las cadenas cortas aciertan más que las largas, hasta un 34,5 %. Hassid et al., 2025,
    <https://arxiv.org/abs/2505.17813>.
- Con poco presupuesto, no pensar o pensar poco compite con pensar mucho. Ma et al., 2025, «Reasoning Models Can Be
  Effective Without Thinking», <https://arxiv.org/abs/2504.09858>.
- Elegir el modo según la dificultad recorta la longitud media un 53 % sin perder precisión, aunque con RL y modelos
  pequeños. AdaptThink, Zhang et al., 2025, <https://arxiv.org/abs/2505.13417>.
- Cortar el pensamiento con un delimitador de fin («budget forcing») degrada la calidad de forma gradual, no brusca.
  s1, Muennighoff et al., 2025, <https://arxiv.org/abs/2501.19393>.

**Mecanismo**
- El presupuesto es por agente, no por llamada. Por eso se declaran dos variantes de los agentes caros, con la misma
  instrucción y el mismo script:
  - `specify_quick` (thinking 256, salida 1.024) y `specify_deep` (1.024 como ahora);
  - igual para requirements (512 / 1.024).
- El script, que ya conoce la relación de cada lugar, elige la variante en su línea «NEXT»:
  - `quick` para un lugar derivado: copia, gemelo async/sync, llamador que solo pasa un argumento nuevo, o
    re-exportación;
  - `quick` también cuando la frase del lugar principal ya existe y el lugar derivado tiene el mismo cuerpo
    normalizado;
  - `deep` para el lugar principal de cada requisito.
- La regla depende del tipo de relación que calcula scope. No depende de nombres ni de una librería concreta.
- Para el coordinador y los pasos de copia (`open`, respuesta final), probar un presupuesto mínimo (por ejemplo 64)
  si el ADK y vLLM lo respetan. Hoy esas llamadas generan de 114 a 185 tokens; el ahorro es pequeño pero no cuesta
  nada.

**Ahorro esperado**
- Si la mitad de las unas 3,5 decisiones de specify por tarea son lugares derivados:
  1,75 × (1.024 − 256) / 36 ≈ 37 s.
- Requirements de 1.024 a 512 cuando el enunciado es corto (pocas frases de comportamiento): unos 14 s por decisión,
  unos 7–10 s por tarea si aplica a la mitad.
- **Total: unos 40–47 s por tarea.** Sobre la propuesta 1 el ahorro baja, porque la ranura «deep» sigue marcando el
  tiempo de cada tanda. Combinadas, conviene que la segunda tanda (los derivados) sea la «quick».

**Riesgo para la calidad**
- Medio-bajo en los lugares derivados: la decisión es casi mecánica y la literatura indica que ahí sobra pensamiento.
- Alto si el script clasifica mal un lugar como derivado. Por eso la regla debe ser conservadora: ante la duda,
  `deep`.
- Un presupuesto que se agota corta el razonamiento a medias. En los lugares derivados conviene que el presupuesto
  quede por encima del uso típico, no por debajo.

**Esfuerzo:** pequeño. Son dos entradas más en la configuración y una regla de enrutado en el script.

**Cómo medirlo**
- Antes de Kaggle, reproducir las mismas vistas de specify sacadas de las trazas reales en el vLLM de Colab: 3
  muestras a 1.024, 512 y 256.
- Comparar los tokens de pensamiento, si se agotó el presupuesto y si la frase coincide en lugar y sentido con el
  parche de referencia (lo juzga una persona o el editor más `judge.py`).
- En la ejecución: tiempo de specify por tarea y número de tareas que llegan a `submit_patch` antes de los 5 min.

**Genérico:** sí, siempre que la regla use el tipo de relación (copia, gemelo, llamador) y nunca nombres concretos.

---

## 3. Flujo declarativo (`SequentialAgent`/`LoopAgent`) en lugar de un coordinador LLM que solo enruta

**Técnica y fuentes**
- Agentless (Xia et al., 2024/FSE 2025, <https://arxiv.org/abs/2407.01489>): un flujo fijo de localizar, reparar y
  validar, sin que un LLM elija la acción, alcanzó el mejor resultado abierto en SWE-bench Lite (32 %) con el menor
  coste.
- Efficient Agents (Wang et al., 2025, <https://arxiv.org/abs/2508.02694>): simplificar el andamiaje conserva el
  96,7 % del rendimiento con un 28 % menos de coste por acierto.
- SWE-Effi (2025, <https://arxiv.org/abs/2509.09853>): la eficacia depende de cómo el andamiaje encaja con el modelo
  y de los recursos gastados por cada intento, no solo de la tasa de resolución.
- Aquí el coordinador ya no decide nada (solo copia la línea «NEXT» del script), así que es puro coste.

**Mecanismo**
- La raíz pasa a ser un `SequentialAgent`:
  `[requirements, locate, scope, specify (ParallelAgent de la propuesta 1), LoopAgent(edit, max_iterations = 6),
  review, finish]`. El último agente llama a `submit_patch`.
- Una etapa que el script decide saltar recibe en `open` «nada que hacer, responde: ok». Es una llamada barata en
  lugar de las tres de ahora (coordinador, `open` y respuesta).
- Paso opcional, el de más ahorro: en un `SequentialAgent` todos los sub-agentes comparten la sesión. Con
  `include_contents` por defecto, el agente siguiente ve como contexto el resultado de la última llamada al script del
  anterior. Si la última respuesta del script de una etapa ya trae la vista de la siguiente, esta decide sin `open`.
  Hay que comprobar, en el ADK del arnés, cómo se presentan al modelo los eventos de otros agentes y cuánto crece el
  historial frente a la compactación a 14.336 tokens. Si crece demasiado, `include_contents: none` y se conserva el
  `open`.
- La vuelta atrás (`back`) se pierde como ruta libre. El script puede ofrecerla como una segunda iteración de un
  `LoopAgent` externo, a cambio de una llamada barata por etapa vacía, o renunciar a ella con menos de 5 min.

**Ahorro esperado**
- Sin el coordinador: −27,6 s por tarea.
- Sin los `open` gracias al historial compartido: hasta −22,7 s.
- Iteraciones vacías del `LoopAgent` y etapas saltadas: unos +8 s (unas 2 llamadas de 4 s).
- **Neto: unos 20 s por tarea si se conservan los `open`, y unos 40 s si se eliminan.**

**Riesgo para la calidad**
- Bajo. No cambia ninguna decisión de contenido y el modelo deja de copiar líneas, donde también puede equivocarse.
- Con el historial compartido aumenta el contexto de cada decisión. Hay riesgo de distracción (Gema et al., 2025) y
  de compactación a media tarea.

**Esfuerzo:** medio. Hay que reescribir la raíz del YAML y cambiar los textos del script en el «NEXT» y el «nada que
hacer».

**Cómo medirlo**
- Llamadas y tiempo por tarea en las categorías del brief: «coordinador», «open» y «respuesta final».
- Tokens de entrada por decisión.
- Número de compactaciones.
- Tareas con `submit_patch` antes de los 5 min.

**Genérico:** sí. Es la forma del flujo, idéntica para cualquier tarea.

---

## 4. LoRA de pensamiento conciso por auto-entrenamiento (el 31B aprende de sus propias trazas más cortas)

**Técnica y fuentes**
- Self-Training Elicits Concise Reasoning (Munkhbat et al., 2025/Findings ACL 2025,
  <https://arxiv.org/abs/2502.20122>): muestrear N cadenas, quedarse con la más corta correcta y ajustar el modelo
  con ellas. Recorta los tokens de salida un 30 % de media en cinco familias de modelos sin perder precisión.
- Hassid et al., 2025 (<https://arxiv.org/abs/2505.17813>): ajustar un 32B con las cadenas más cortas rinde más que
  con las largas o aleatorias.
- TokenSkip (Xia et al., 2025/EMNLP 2025, <https://arxiv.org/abs/2502.12067>): un ajuste ligero sobre cadenas
  comprimidas recorta un 40 % del razonamiento con menos de 0,4 puntos de pérdida (Qwen2.5-14B, GSM8K).
- Distilling System 2 into System 1 (Yu et al., 2024, <https://arxiv.org/abs/2407.06023>): el mismo modelo destila su
  razonamiento largo en respuestas directas.
- Todas usan solo salidas del propio modelo, así que cumplen la regla de no entrenar con salidas de LLM comerciales
  ajenos.

**Mecanismo**
- Para cada tipo de decisión (specify, requirements, locate), recoger las vistas reales del procedimiento en:
  - las 129 tareas de desarrollo;
  - más repositorios públicos con incidencias y pruebas, usando solo las instancias y nunca sus trayectorias, que
    suelen venir de modelos comerciales: SWE-Gym, <https://arxiv.org/abs/2412.21139>; SWE-smith,
    <https://arxiv.org/abs/2504.21798>.
- Muestrear N = 8 respuestas del 31B en el vLLM de Colab con el presupuesto actual.
- Etiquetar cada decisión con un oráculo genérico:
  - locate: el lugar elegido está en el parche de referencia;
  - specify: la edición derivada pasa `judge.py`, o al menos toca las mismas líneas que la referencia.
- Quedarse con el pensamiento y la respuesta más cortos entre los correctos.
- Entrenar un LoRA (rango 16–64) solo para los agentes de decisión y declararlo en `adapters/`.

**Ahorro esperado**
- Unos 155 s por tarea son pensamiento de decisiones (182 × 0,85). Un 30 % menos son unos 45 s.
- Hay que descontar el coste del LoRA en la decodificación de vLLM. No encontré una cifra publicada para vLLM con una
  sola petición, y en otros sistemas los adaptadores dinámicos han llegado a ralentizar la decodificación varias veces
  (LoRA-Switch, 2024, <https://arxiv.org/abs/2405.17741>).
- **Neto: entre unos 20 y unos 45 s por tarea, con incertidumbre alta.** Si el LoRA ralentiza más de un 25 % por
  token, no compensa.

**Riesgo para la calidad**
- Medio-alto. Con 129 tareas de 4 librerías hay riesgo de sobreajuste, y la prueba usa repositorios privados.
- Si el oráculo de corrección es débil (sobre todo en specify), se refuerzan decisiones cortas pero erróneas.
- El ajuste se hace sobre pesos distintos de los de Kaggle (QAT o bf16 frente a w4a16), lo que puede desalinear el
  adaptador.

**Esfuerzo:** grande. Requiere la recogida de datos con el modelo, el oráculo por etapa, QLoRA de un 31B (cabe en la
G4 de 96 GB), servirlo en vLLM con LoRA y validarlo.

**Cómo medirlo**
- Primero, en Colab: tok/s de una petición con y sin el adaptador cargado. Si la caída es mayor del 15–20 %, abandonar.
- Después, con los mismos prompts reservados (tareas no usadas al entrenar): tokens de pensamiento, tasa de presupuesto
  agotado y acierto de la decisión.
- Finalmente, una ejecución de Kaggle comparada con la última.

**Genérico:** sí en el método, porque enseña a pensar más corto en el formato del procedimiento y no en un repositorio
concreto. Hay que vigilarlo con tareas de repositorios no vistos.

---

## 5. Propagación determinista de ediciones a copias idénticas (sin especificar ni editar dos veces)

**Técnica y fuentes**
- CodePlan (Bairi et al., 2023/FSE 2024, <https://arxiv.org/abs/2309.12499>): tras una edición, un análisis de
  dependencias e impacto deriva las ediciones de los lugares afectados en lugar de tratarlos como tareas nuevas.
- CoEdPilot (Liu et al., ISSTA 2024, <https://arxiv.org/abs/2408.01733>): las ediciones siguientes se predicen a
  partir de las anteriores.
- Aquí el brief dice que las copias del mismo código en dos paquetes multiplican las llamadas de specify y de edit.

**Mecanismo**
- Cuando scope encuentra un lugar cuyo cuerpo normalizado coincide con el de un lugar ya editado, el script trasplanta
  la edición sin llamar al modelo. «Normalizado» significa sin espacios ni comentarios, con `async`/`await` y los
  nombres de la propia definición abstraídos.
- El trasplante aplica el mismo diff, adaptando `await` y los nombres. Después el script comprueba la sintaxis, pasa
  las pruebas relacionadas y deshace si fallan, como ya hace con las ediciones del modelo.
- Si el trasplante falla, el lugar vuelve al circuito normal: specify «quick» (propuesta 2) y edit.
- En review, el modelo ve el lugar marcado como «copiado de X» y puede rechazarlo.

**Ahorro esperado**
- Por cada copia evitada: una decisión de specify (unos 28 s) y una invocación de edit (unos 9 s de decisión más unos
  13 s fijos), unos 50 s en serie. Con la propuesta 1, specify ya va en paralelo y el ahorro baja a unos 22 s, la
  parte de edit.
- Si una de cada tres tareas tiene una copia de este tipo: **unos 7–17 s por tarea de media**, y más de 40 s en las
  tareas que la tienen.

**Riesgo para la calidad**
- Bajo si la igualdad exigida es estricta y las pruebas se ejecutan tras el trasplante.
- La copia no siempre debe cambiar igual (por ejemplo, la variante async puede necesitar otra cosa). Review y las
  pruebas son la red de seguridad.

**Esfuerzo:** medio. Requiere la normalización y el trasplante del diff en el script.

**Cómo medirlo**
- Número de lugares resueltos por trasplante y cuántos de ellos se deshicieron o rechazó review.
- Llamadas de specify y de edit por tarea.
- Resolución en las tareas con copias, comparada con la ejecución anterior.

**Genérico:** sí. Se basa en la igualdad estructural del código, sin nombres ni librerías concretas.

---

## Ideas consideradas y descartadas

**Requieren opciones de vLLM o lógica durante la decodificación que la configuración no permite**
- **Decodificación especulativa** (modelo borrador, MTP, n-gramas o «prompt lookup»): serían opciones del servidor
  vLLM, que no controlamos.
- **Salida temprana dinámica del pensamiento** (DEER, Yang et al., 2025, <https://arxiv.org/abs/2504.15895>, y
  métodos parecidos basados en la confianza): necesitan inspeccionar los logits o intervenir en la decodificación, y no
  hay callbacks con lógica.
- **Cortar el pensamiento con una secuencia de parada:** la parada termina toda la generación, no solo el
  pensamiento, y el modelo se quedaría sin respuesta.

**Mejoran la calidad o la estructura, pero no el tiempo**
- **Votación paralela de cadenas cortas** (short-m@k, Hassid et al., 2025): mejora la precisión, pero ahorra tiempo
  solo si se detienen las ramas lentas. `ParallelAgent` espera a todas, así que no ahorra.
- **NoThinking tal cual** (Ma et al., 2025): exige rellenar el inicio de la respuesta del asistente, cosa que la
  configuración no permite. Su lección se recoge en la propuesta 2: presupuestos bajos para decisiones fáciles.
- **Más intentos y selección al estilo Agentless** (varios parches y elegir uno): cuesta más tiempo, al revés de lo
  buscado.

**Atacan una parte del tiempo que aquí casi no pesa**
- **Comprimir el contexto** (LLMLingua y similares): el prefill es rápido y la caché de prefijos acierta el 79 %. El
  tiempo está en generar, no en leer.
- **Pedir en el prompt que piense corto** (Chain of Draft, Xu et al., 2025, <https://arxiv.org/abs/2502.18600>;
  TALE, Han et al., 2024, <https://arxiv.org/abs/2412.18547>): funciona sobre el razonamiento visible, pero los modelos
  de razonamiento siguen mal estas instrucciones dentro del canal de pensamiento. Se puede probar gratis junto a la
  propuesta 2, aunque espero poco efecto.

**Esfuerzo o encaje fuera de las restricciones**
- **RL con penalización de longitud** (L1/LCPO, <https://arxiv.org/abs/2503.04697>; ThinkPrune; AdaptThink): exige
  RL sobre un 31B, demasiado esfuerzo e infraestructura para el plazo. El auto-entrenamiento supervisado de la
  propuesta 4 consigue parte del efecto con mucho menos.
- **Un modelo pequeño para las decisiones fáciles:** solo se permite un modelo base.
- **`skip_summarization` en `agent_tool`** para ahorrar la respuesta final: su efecto en el ADK sería terminar el
  turno del coordinador, no ahorrar una llamada. Es incierto y frágil. La propuesta 3 elimina el problema de raíz.

## Orden sugerido para probarlas

1. Medir f con 4 peticiones simultáneas al vLLM de Colab: decide si la propuesta 1 vale lo que promete.
2. Propuesta 2: es la de menor esfuerzo, y su comparación de presupuestos se puede hacer sin Kaggle.
3. Propuestas 1 y 3 juntas: es un único cambio en el YAML.
4. Propuesta 5.
5. Propuesta 4, solo si tras lo anterior siguen muchas tareas cortadas a los 5 min.
