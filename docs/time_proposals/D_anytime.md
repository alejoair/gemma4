# Propuestas D: estrategias de "cualquier momento" y conscientes del presupuesto

Ángulo: el corte a los 5 minutos se queda con el `git diff` que exista en ese instante. La puntuación es binaria (resuelto o no) y un parche vacío vale 0 seguro, igual que uno equivocado. Por tanto, **cualquier parche es al menos tan bueno como ninguno**, y el orden del trabajo debe hacer que el parche más probable exista cuanto antes y que el tiempo posterior solo lo mejore (algoritmos de cualquier momento, *anytime*, Zilberstein 1996: calidad monótona en el tiempo, perfil de rendimiento y asignación por valor marginal).

## Números de partida (del brief, por tarea, media de 10)

- 27 llamadas al modelo, 263,5 s de modelo; 7 de 10 tareas cortadas a los 300 s; 4 de 10 con parche vacío (las 4 entre las cortadas).
- Coste fijo de invocar un sub-agente (fontanería): `open` 5,5 s (227/41) + respuesta final 4,4 s (235/54) + enrutado del coordinador 3,5 s (276/80). En total, **unos 13,4 s por invocación**, aparte de sus decisiones.
- Una decisión de especificar gasta todo su presupuesto de pensamiento: 1.024 tokens / 36 tok/s ≈ **28 s**. Una decisión media de sub-agente ≈ 20 s (1.822/91).
- Tiempo por etapa y tarea: requisitos 34 s, localizar 27,5 s, alcance 21 s, especificar 124 s, editar 23 s, revisar 7 s y coordinador 27,6 s.
- **Momento del primer diff hoy** (orden por etapas: se especifican todos los lugares y después se edita):
  - Prefijo: requisitos, localizar y alcance suman 83 s, más unos 17 s de enrutado. Total, unos 100 s.
  - Especificar todo: 124 s de media.
  - Abrir el editor y decidir: unos 15 s.
  - **El primer diff aparece hacia los 240 s.**
  - Con un plan de 5 lugares, especificar ya cuesta 13 + 5×28 ≈ 153 s. La edición empieza hacia los 255 s y cada lugar editado cuesta unos 28 s, así que a los 300 s hay 1 o 2 lugares editados.
  - Con 6 lugares, ninguno. Esto encaja con los 4 parches vacíos.

Conclusión: el orden por etapas en anchura (todos los lugares en cada etapa antes de pasar a la siguiente) es el peor orden posible con un corte duro. El tiempo de especificar crece con el número de lugares y el primer byte de parche llega casi al final.

---

## Propuesta 1 (la de mayor efecto): dos oleadas en profundidad, primero el lugar principal

**Técnica.** Cómputo de cualquier momento por refinamiento progresivo, con el trabajo ordenado por valor esperado (primero en profundidad sobre el lugar más probable). Es lo que hace Agentless (Xia et al. 2024: localizar jerárquicamente y reparar primero lo más probable), pero sin muestreo múltiple.

**Mecanismo** (solo cambia la máquina de estados del script; la configuración ADK y los agentes no cambian):

1. **Ola 1.** Se ejecutan requisitos y localizar. Después, **especificar solo el lugar principal**: el primer candidato elegido para el primer requisito, en una invocación con un único objetivo. Luego **editar ese lugar**. El árbol de trabajo ya tiene un parche plausible.
2. **Ola 2.** Se ejecuta alcance, que pasa a ir detrás de la primera edición, porque solo lo necesita la ola 2. Después, especificar el resto de lugares en **una sola invocación**, como hoy, y editarlos uno a uno en orden de valor (propuesta 2). Por último, revisar si queda tiempo.
3. El especificador de la ola 2 ve el diff ya aplicado del lugar principal como contexto fijo. Así ve código concreto en lugar de una intención, y sus frases quedan coherentes con él (el nombre de un parámetro nuevo, por ejemplo).

**Efecto esperado.**
- **Primer diff:** requisitos 34 s + localizar 27,5 s + enrutado unos 7 s + especificar 1 lugar (13,4 + 28 ≈ 41 s) + editar (13,4 + 15 ≈ 28 s) ≈ **138 s**, frente a unos 240 s hoy. Llega unos 100 s antes y con 160 s de margen hasta el corte.
- **Coste total:** una invocación extra de especificar (+13,4 s). Es lo único que se pierde al final, y se recupera si la propuesta 4 o la 2 recortan lugares.
- **Tareas con parche vacío:** las 4 de cada 10 pasan a tener al menos el lugar principal editado.
- **Cuántas resolverían:** la probabilidad de que un parche de un solo lugar resuelva se puede medir sin el modelo, con los 129 parches de referencia del conjunto de desarrollo. Hay que contar en qué fracción la referencia toca una sola función o clase. En SWE-bench, la mayoría de los parches de referencia tocan un solo archivo, y muchos, una sola función.
- **Estimación:** hoy resuelven 3 de los 6 parches no vacíos. Para un parche parcial, la tasa condicionada puede rondar el 20–35 %. Eso da 4 × 0,25 ≈ **+1 tarea por cada 10** (rango de +0,5 a +1,5). También ayuda algo a las 3 tareas cortadas con parche no vacío pero incompleto, porque lo editado es lo más valioso.

**Riesgo para la calidad.**
- **Bajo a medio.** El especificador del lugar principal pierde la vista simultánea de todos los lugares, que se introdujo para que las frases fueran coherentes entre sí.
- Lo mitiga que el especificador de la ola 2 vea el diff ya hecho. Además, el lugar principal es justo el que menos depende de los demás: los otros son copias, gemelos o llamadores de él.
- **Riesgo residual:** un cambio de interfaz que el lugar principal debería decidir mirando a sus llamadores. Para eso, la vista de la ola 1 puede seguir mostrando los llamadores como contexto de lectura; no se especifican, solo se ven.

**Esfuerzo.** Pequeño a medio: reordenar transiciones en la máquina de estados y permitir que especificar reciba un subconjunto de lugares.

**Cómo medirlo.**
- El script guarda, en cada edición aplicada, una instantánea del diff con su marca de tiempo. Debe guardarse fuera del repositorio, por ejemplo en `/tmp` del sandbox, para que no entre en el parche.
- Con ellas se mide el **tiempo hasta el primer diff no vacío**, el número de tareas con parche vacío al corte y el número de resueltas.
- Además, juzgando cada instantánea se obtiene el **perfil de rendimiento**: la curva P(resuelto si el corte fuera en t). Se compara contra la versión actual en las mismas 10 tareas.

**Genérico.** Sí. No depende del repositorio ni de la incidencia, solo del orden de las etapas.

---

## Propuesta 2: planificador con reloj en el script (presupuesto restante → qué se hace y qué se salta)

**Técnica.** Control de meta-nivel con un modelo de costes. En cada transición se elige la acción de mayor valor por segundo que cabe en el tiempo restante (Zilberstein; "budget-aware tool use", BATS, Google 2025: un rastreador de presupuesto explícito mejora a los agentes con presupuesto fijo). Aquí la decisión no la toma el modelo sino el **script**: es determinista y no se le pide al 31B que estime tiempos, algo que hace mal.

**Mecanismo.**
- La primera llamada al script guarda `t0` en el fichero de estado. Cada transición calcula el tiempo restante R = 300 − (ahora − t0) − 10 s de margen.
- Tabla de costes medida (se recalibra con cada ejecución):
  - invocación de un sub-agente: 13,4 s;
  - decisión: presupuesto de pensamiento / 36 + salida / 36 (especificar ≈ 28 s, editar ≈ 15–20 s, localizar o alcance ≈ 14 s).
- Reglas:
  1. **Orden de lugares por valor**:
     - el principal;
     - luego sus copias y gemelos async/sync;
     - luego los añadidos a petición del especificador;
     - por último, llamadores, overrides y exports.

     El orden se calibra sin el modelo: en los 129 parches de referencia, la fracción de veces que la referencia toca cada tipo de lugar relacionado.
  2. **Corte del plan:** un lugar entra en la ola actual solo si el coste acumulado (especificar + editar) cabe en R. Los que no caben se descartan sin llamar al modelo.
  3. **Alcance** se salta si R no alcanza para alcance (unos 21 s) más un lugar adicional completo (unos 60 s).
  4. **Revisar** solo si R da para revisar más una corrección (unos 50 s). Si no, se pasa a `submit_patch` o se deja correr, porque el diff se toma igualmente al corte.
  5. **No reservar tiempo para enviar:** el arnés toma el diff al cortar, así que la última acción útil es siempre otra edición, nunca un cierre "ordenado".
  6. **Reproducir** (hoy saltado) solo si al terminar la ola 1 queda R > 150 s.
- El script ya decide la siguiente llamada; esto solo añade la condición de tiempo. El coordinador sigue copiando una línea.

**Efecto esperado.**
- Los segundos que hoy se gastan en alcance, revisión o lugares de poco valor cuando no hay tiempo para usarlos (alcance 21 s, revisar 7 s y especificar lugares que nunca se editarán) se convierten en ediciones del lugar siguiente en valor.
- En una tarea de 5 lugares hoy se especifican los 5 (unos 153 s) y se editan 1 o 2. Con el corte del plan solo se especifican los que caben editados, y se ahorran 2 o 3 especificaciones × 28 s ≈ 56–84 s, que pasan a ediciones.
- Estimación: **+0,3 a +0,5 tareas por cada 10** sobre la propuesta 1. Son tareas de varios lugares donde hoy se especifica de más y se edita de menos.

**Riesgo para la calidad.**
- **Bajo.** No cambia ninguna decisión del modelo, solo cuáles se le piden.
- El riesgo es de **calibración**: si la tabla de costes infravalora, se corta tarde. El margen de 10 s y la recalibración por ejecución lo acotan.
- Descartar un lugar necesario de bajo rango hoy tampoco se editaría, porque el corte llega antes.

**Esfuerzo.** Pequeño a medio (una tabla de costes, el reloj en el estado y condiciones en las transiciones).

**Cómo medirlo.**
- Por tarea: lugares planificados, especificados y editados al corte.
- La fracción de especificaciones "huérfanas" (especificadas y nunca editadas). Hoy se puede contar en las trazas; debería bajar a cerca de 0.
- Segundos gastados después de la última edición aplicada.
- El perfil de rendimiento de la propuesta 1.

**Genérico.** Sí (tiempo y tipos de lugar, nunca nombres).

---

## Propuesta 3: dos velocidades por agente, elegidas por el reloj (presupuesto de pensamiento según el tiempo restante)

**Técnica.** Escalado del cómputo en prueba según el presupuesto. El pensamiento se recorta cuando el valor marginal de pensar más es menor que el de cubrir otro lugar ("budget forcing", s1, Muennighoff et al. 2025; TALE, Han et al. 2024: razonamiento consciente del presupuesto de tokens con poca pérdida). Como el `thinking_budget` es estático por agente, la variación se consigue **duplicando agentes**.

**Mecanismo.**
- Se declaran agentes gemelos con otra configuración de generación y la misma instrucción y el mismo script:
  - `specify_fix_rapido` (pensamiento 384, frente a 1.024);
  - `edit_place_rapido` (384);
  - opcionalmente `locate_rapido` (256).
- El planificador de la propuesta 2 escribe en la línea NEXT el nombre del agente según R:
  - **ola 1** (lugar principal): siempre la versión completa, porque es el lugar de más valor;
  - **ola 2**: la versión completa si R alcanza para todos los lugares del plan; si no, la rápida.
- Encaja con la regla de un solo paso acotado y no cambia nada de lo que el modelo ve.

**Efecto esperado.**
- Especificar a 1.024 tokens tarda unos 28 s; a 384 tokens, unos 11 s. Se ahorran unos 17 s por lugar especificado en modo rápido.
- Editar ahorra unos 8–17 s por lugar, según cuánto pensamiento gaste hoy.
- En la cola, un lugar completo pasa de unos 60 s a unos 35 s. En los últimos 100 s caben 3 lugares en vez de 1 o 2.
- Estimación: **+0,2 a +0,4 tareas por cada 10**, en tareas de 3 o más lugares. En la cola, la alternativa a una decisión rápida no es una decisión lenta: es **ninguna edición** (valor 0).

**Riesgo para la calidad.**
- **Medio en el lugar en que se usa.** El brief dice que especificar suele agotar el presupuesto, y cortar el pensamiento a mitad puede dar una frase peor.
- Por eso no se aplica nunca al lugar principal, solo a lugares secundarios (copias, gemelos, llamadores), cuyo cambio suele seguir al principal y pide menos razonamiento.
- No usar en `max_output_tokens`: cortar la salida rompe la llamada a la herramienta.

**Esfuerzo.** Pequeño (agentes YAML duplicados más la elección en el script).

**Cómo medirlo.**
- Comparar, por modo (completo o rápido), los tokens de pensamiento por decisión, la tasa de ediciones deshechas por los tests y la tasa de llamadas mal formadas.
- Medir los lugares editados al corte y las tareas resueltas.
- Si el modo rápido sube las ediciones deshechas más de lo que añade lugares, se sube a 512.

**Genérico.** Sí.

---

## Propuesta 4: propagación determinista a copias idénticas (cero llamadas al modelo)

**Técnica.** Saltar trabajo redundante. Si un lugar es una copia textual de un lugar ya editado, la decisión ya está tomada: el script la replica (como `patch` con contexto), sin especificar ni editar con el modelo.

**Mecanismo.**
- Al aplicar una edición del modelo en el lugar A, el script busca entre los lugares pendientes los que cumplen dos condiciones:
  - su texto original es **idéntico** al original de A, tras normalizar la sangría y los espacios;
  - o el diff de A aplica limpiamente sobre ellos con difflib.
- A esos lugares les aplica el mismo cambio, comprueba la sintaxis, pasa los tests relacionados y deshace si rompen, igual que una edición del modelo.
- Los marca como hechos con la nota "propagado desde A", visible en el mapa de cobertura.
- Si no aplica limpiamente, el lugar vuelve a la cola normal.
- Gemelos async/sync: un segundo intento con la transformación mecánica (quitar `async`/`await` o añadirlos) y la misma comprobación. Si falla, va al modelo.

**Efecto esperado.**
- Cada copia propagada ahorra especificar (unos 28 s) + editar (unos 15–20 s) + una invocación de editor (13,4 s) ≈ **55–60 s**.
- El brief dice que las copias en dos paquetes y los gemelos multiplican las llamadas. Si un 30 % de las tareas tiene al menos una copia o gemelo propagable, el ahorro medio es de unos 18 s por tarea, pero concentrado: en esas tareas son casi 60 s, justo el lugar que hoy no llega a editarse.
- Estimación: **+0,2 a +0,4 tareas por cada 10**. Hay que medir antes la fracción de planes con copias en las trazas o en los 129 parches de referencia: cuántas referencias editan dos definiciones textualmente iguales.

**Riesgo para la calidad.**
- **Bajo.** No hay decisión del modelo. El riesgo es que la copia necesite un cambio distinto, por ejemplo otra API en el otro paquete.
- Lo acotan la exigencia de identidad o de aplicación limpia y los tests relacionados.
- Además, el especificador de la ola 2 puede ver "propagado" y pedir `back` si hace falta.

**Esfuerzo.** Medio (detección de copias, aplicación con contexto y transformación async).

**Cómo medirlo.**
- Lugares propagados por tarea y su tasa de deshechos.
- Llamadas a especificar y editar por tarea (deberían bajar en tareas con copias).
- Si el parche resultante coincide con la referencia en esos lugares (comparando los ficheros tocados).

**Genérico.** Sí (igualdad textual y transformación sintáctica, sin nombres).

---

## Propuesta 5: especificadores en paralelo en la ola 2 (lotes en vLLM)

**Técnica.** Planificación en paralelo para aprovechar el lote continuo de vLLM. Hoy vLLM atiende una petición cada vez y la caché KV está al 2 %. La decodificación de un 31B W4A16 está limitada por el ancho de banda de memoria, así que 2–3 flujos simultáneos suelen dar 2–2,5 veces más tokens por segundo en total, con poca pérdida por flujo.

**Mecanismo.**
- Un `ParallelAgent` con 3 especificadores fijos (`especificar_ranura_1..3`), envuelto como `agent_tool` del coordinador.
- La ranura k llama al script con su número y toma el k-ésimo lugar pendiente de la ola 2.
- Cada ranura escribe su frase en un fichero propio, para evitar carreras sobre el estado JSON. El script las une en la siguiente transición.
- Solo especificar va en paralelo, porque solo lee. Las ediciones siguen en serie, porque comparten `/workspace` y los tests.
- Si el plan tiene menos lugares que ranuras, las ranuras sobrantes reciben "nada que hacer" y responden en una línea, lo que cuesta unos 5 s concurrentes.

**Efecto esperado.**
- Ola 2 de 3 lugares: hoy cuesta 13,4 + 3×28 ≈ 97 s. En paralelo, si cada flujo baja de 36 a unos 30 tok/s, cuesta 13,4 + 34 ≈ 48 s.
- Se ahorran **unos 50 s por tarea de 4 o más lugares**, que pasan a ediciones. Estimación: +0,3 a +0,6 tareas por cada 10, **si es viable**.

**Riesgo para la calidad.**
- **Bajo** para las decisiones: cada ranura ve lo mismo que un especificador normal.
- El riesgo es de **viabilidad**:
  - que un `ParallelAgent` funcione como `agent_tool` y entregue una respuesta de texto utilizable;
  - que vLLM ejecute realmente las peticiones en lote con la configuración de Kaggle.
- Por eso va en último lugar.

**Esfuerzo.** Medio. Antes, una prueba de 30 minutos: 3 peticiones simultáneas al vLLM de Colab para medir tok/s por flujo y en total, y una configuración mínima de `ParallelAgent` como herramienta en el arnés local.

**Cómo medirlo.** Tok/s agregados durante la ola 2, tiempo de pared de la ola 2 y la misma curva de perfil de rendimiento.

**Genérico.** Sí.

---

## Resumen del orden y del efecto combinado

| # | Propuesta | Efecto estimado (resueltas/10) | Riesgo de calidad | Esfuerzo |
|---|---|---|---|---|
| 1 | Dos oleadas, lugar principal primero | +0,5 a +1,5 | bajo-medio | pequeño-medio |
| 2 | Planificador con reloj y valor por lugar | +0,3 a +0,5 | bajo | pequeño-medio |
| 3 | Dos velocidades según el reloj | +0,2 a +0,4 | medio (solo en lugares secundarios) | pequeño |
| 4 | Propagación determinista a copias | +0,2 a +0,4 | bajo | medio |
| 5 | Especificadores en paralelo | +0,3 a +0,6 si es viable | bajo (viabilidad incierta) | medio |

Los efectos no son aditivos (compiten por las mismas tareas de varios lugares). Una estimación prudente del conjunto 1+2+4 es de 3/10 a 4–5/10. Todas usan el mismo instrumento de medida, que conviene construir primero porque cuesta poco: **instantáneas del diff con marca de tiempo tras cada edición y su juicio por separado**. Dan la curva P(resuelto | corte en t) y el tiempo hasta el primer diff, y permiten comparar versiones sin esperar a que el número de resueltas, con 10 tareas, sea significativo.

## Ideas consideradas y descartadas

- **Darle al modelo el reloj ("te quedan N s").** Un LLM estima mal el tiempo y el coste de sus pasos, y añade una decisión más. Es mejor que el script decida (propuesta 2) y el modelo siga con un único paso acotado.
- **Reservar tiempo al final para revisar y enviar.** El arnés toma el diff al cortar, así que reservar tiempo para cerrar solo quita ediciones. Revisar ya es raro (5 llamadas en 10 tareas).
- **Borrador sin especificar (que el editor edite el lugar principal directamente desde el enunciado).** Saldría el diff unos 40 s antes, pero las trazas ya mostraron editores que leen en lugar de decidir cuando no tienen la frase. Contradice la lección del diseño en microtareas.
- **Recortar `max_output_tokens` para forzar respuestas cortas.** Corta llamadas a herramienta a mitad, deja llamadas mal formadas y obliga a repetir: pierde más tiempo del que ahorra.
- **Varios intentos completos y elegir el mejor (best-of-n, muestreo múltiple de Agentless).** No cabe ni un intento completo en 300 s.
- **Generar especulativamente ediciones para los 3 primeros candidatos antes de elegir.** Con generación en serie es puro gasto; solo tendría sentido con lotes (propuesta 5) y aun así triplica el trabajo de la etapa más cara.
- **Saltar siempre la etapa de requisitos.** Cuesta 34 s, pero guía la localización y la revisión. Bajo el reloj (propuesta 2) se podría acortar con el agente rápido, pero eliminarla quita la base del plan.
- **Una LoRA que piense menos.** Probablemente efectiva, pero es esfuerzo grande, está sujeta a la regla de datos (no puede entrenarse con salidas de otros LLM comerciales) y su efecto es el mismo que la propuesta 3, que cuesta una línea de YAML.
- **Secuencias de parada para cortar el pensamiento.** Se aplican a la salida visible. Interferir con el canal de razonamiento arriesga romper el analizador de gemma4 en vLLM.
- **Aplicar las ediciones en una copia del repositorio y moverlas solo tras pasar los tests**, para que un corte a mitad de los tests no deje una edición sin validar. Ayuda poco: una edición sin validar vale más que ninguna. Solo merece la pena si las trazas muestran cortes a mitad de los tests que dejan errores de importación.

## Referencias

- Zilberstein, S. (1996). Using anytime algorithms in intelligent systems. AI Magazine 17(3).
- Xia, C. S. et al. (2024). Agentless: Demystifying LLM-based software engineering agents.
- Muennighoff, N. et al. (2025). s1: Simple test-time scaling (budget forcing).
- Han, T. et al. (2024). Token-budget-aware LLM reasoning (TALE).
- Liu, T. et al. (2025). Budget-aware tool-use enables effective agent scaling (BATS).
