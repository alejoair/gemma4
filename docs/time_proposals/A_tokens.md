# Propuestas A: economía de tokens y configuración de generación

Ángulo: cuántos tokens genera el modelo en cada llamada (pensamiento y salida visible) y cómo bajarlos sin empeorar las decisiones.

## Cuentas de partida (del brief)

- Por tarea: 27 llamadas, 8.600 tokens generados (85 % pensamiento), 264 s de llamadas al modelo. A 36 tok/s, 8.600 tokens son 239 s: **casi todo el tiempo es generación, y casi toda la generación es pensamiento.** Cada llamada añade además ≈1 s fijo (2.635 s / 270 llamadas = 9,8 s por llamada, de los que 8,8 s son tokens).
- Las llamadas mecánicas (coordinador, `open` y la respuesta final que copia una línea) son 175 de 270 llamadas y 23.200 tokens: **74 s por tarea (28 %) en llamadas que no deciden nada.**
- `specify` gasta 124 s por tarea (47 %). Mis cuentas: ≈35 decisiones de ≈1.100 tokens (llegan al tope de 1.024), a ≈31 s cada una, es decir ≈3,5 por tarea y ≈108 s; el resto son `open` y respuestas finales.
- 7 de 10 tareas llegaron al límite de 5 min. Cada segundo ahorrado sirve para llegar a `edit` y a `submit`, no para acabar antes.

Ordenadas por ahorro esperado por tarea (el orden para probarlas va al final):

---

## 1. Pensamiento de `specify`: tope más bajo y conclusión primero

**Técnica.** Forzar un presupuesto de pensamiento (*budget forcing*: s1, Muennighoff et al. 2025) junto con un pensamiento consciente del presupuesto y en forma de borrador (TALE, Han et al. 2024; *Chain of Draft*, Xu et al. 2025). Hoy la llamada de `specify` gasta lo que permite el tope; su tiempo lo fija el tope, no la dificultad.

**Mecanismo.**
- `thinking_budget` de `specify` baja de 1.024 a 640 (en un segundo paso, 512). `max_output_tokens` pasa a tope + 1.024; en vLLM ese límite incluye el pensamiento.
- Problema: vLLM corta el pensamiento en seco al llegar al tope (inserta el token de cierre a mitad de frase) y lo que venía después de la última conclusión se pierde. Por eso cambia también la redacción, en la instrucción del especificador y en la vista que da el script:
  > «Piensa en borrador: primero escribe en una línea la frase candidata para este sitio; después compruébala contra el código mostrado y corrígela solo si falla. Tienes unos 600 tokens de pensamiento.»
- Con esta redacción, si el corte llega, ya hay una frase escrita. La forma de la respuesta no cambia (una frase por sitio).

**Ahorro esperado.** Con 3,5 decisiones por tarea:
- de 1.024 a 640 se ahorran 384 tokens por decisión: 1.344 tokens, **≈37 s por tarea**;
- de 1.024 a 512 se ahorran 512 tokens por decisión: 1.792 tokens, **≈50 s por tarea**.

**Riesgo para las decisiones: medio.** `specify` es la decisión difícil. Hoy ya se corta a 1.024 y aun así 3 de 10 tareas se resuelven, así que el modelo ya decide con pensamiento truncado. Un tope más bajo trunca más.
- Mitigación: la conclusión llega antes en el pensamiento, y el tope baja en dos pasos (640 y después 512), no de golpe.
- Evidencia: en s1 y TALE la precisión cae poco al recortar presupuesto en tareas donde el modelo piensa de más. *Do NOT think that much for 2+3=?* (Chen et al. 2024) documenta que gran parte del pensamiento largo repite comprobaciones.

**Esfuerzo:** pequeño (dos campos de configuración y un párrafo de instrucción).

**Cómo medirlo.** Las mismas 10 tareas, dos pasadas por variante (la temperatura es 1,0). Para cada decisión de `specify`:
- tokens de pensamiento y porcentaje de llamadas que tocan el tope;
- frase correcta o incorrecta frente al parche de referencia (lo juzga una persona o el juez de la competición);
- tareas que llegan a `submit` antes de 5 min, y resueltas.

Criterio: que no baje la proporción de frases correctas.

**Genérica:** sí. Cambia un presupuesto y una forma de pensar, independientes del repositorio y de la incidencia.

---

## 2. Llamadas mecánicas con pensamiento casi nulo y líneas más cortas

**Técnica.** Quitar el pensamiento donde no hay decisión, y no hacer que el modelo copie texto que el script ya tiene.

**Mecanismo.**
- **Coordinador:** `thinking_budget: 0`, o 16–32 si 0 no se respeta (ver el riesgo). Solo copia la línea que dicta el script. La guía oficial de Gemma 4 dice que los modelos grandes (31B) pueden abrir un canal de pensamiento aunque esté apagado y recomienda un canal de pensamiento vacío. Un tope de 0 en vLLM equivale a eso: abre el canal y lo cierra enseguida. `max_output_tokens` del coordinador: 256.
- **Línea siguiente más corta.** El estado vive en el fichero JSON del sandbox, así que la petición a cada sub-agente puede ser fija y mínima (por ejemplo, la palabra `sigue`). La línea que el sub-agente devuelve al final pasa a ser solo el nombre del sub-agente siguiente (`→ edit_place`). Hoy los parámetros se copian dos veces: en la respuesta final y en la llamada del coordinador.
- **`open` y respuesta final de los sub-agentes.** No se les puede quitar el pensamiento, porque el tope es por agente y la decisión lo necesita. Sí se puede bajar con la redacción:
  - en la instrucción: «Tu primera llamada es exactamente: run_skill_script(…, open). No hace falta razonar antes.»;
  - en la última respuesta del script: «Responde solo con esta línea, sin razonar: → edit_place».

  La guía de Gemma 4 indica que una instrucción de pensamiento «LOW» reduce los tokens de pensamiento en torno a un 20 %. Aquí la indicación es más fuerte («no hace falta razonar»), porque la acción está fijada.

**Ahorro esperado.**

| Llamadas | Ahora | Después | Ahorro total | Por tarea |
|---|---|---|---|---|
| Coordinador (80) | 118 tokens | ≈40 tokens (solo la llamada) | 6.240 tokens, 173 s | ≈17 s |
| Respuestas finales (54) | 114 tokens | 40–75 tokens (línea más corta y menos pensamiento) | 2.100–4.000 tokens, 60–110 s | 6–11 s |
| `open` (41) | 185 tokens | 100–140 tokens | 1.800–3.500 tokens, 50–95 s | 5–9 s |
| **Total** | | | | **≈28–37 s** |

**Riesgo para las decisiones: bajo.** Estas llamadas no deciden nada.
- Riesgo propio: más llamadas malformadas sin pensamiento previo (nombre de herramienta o argumentos mal escritos).
- Riesgo técnico: que la capa ADK→LiteLLM→vLLM trate `thinking_budget: 0` como «sin fijar» (0 es falso en Python) y no ponga límite. Hay que verificarlo en la primera traza; si pasa, usar 16 o 32.

**Esfuerzo:** pequeño (configuración del coordinador, dos frases de instrucción y el texto de las líneas que devuelve el script).

**Cómo medirlo.** Por tipo de llamada (coordinador, `open`, respuesta final):
- tokens de pensamiento y de salida por llamada;
- porcentaje de llamadas malformadas o rechazadas (hoy 4 de 270);
- llamadas del coordinador que no coinciden con la línea del script.

**Genérica:** sí. Toca solo el enrutado, que es igual en cualquier tarea.

---

## 3. Una sola especificación y una sola edición por grupo de copias

**Técnica.** No generar dos veces los mismos tokens. Las copias del mismo código en dos paquetes y los gemelos async/sync multiplican hoy las llamadas de `specify` y de `edit`. El brief lo señala como causa de planes de 2–6 sitios.

**Mecanismo (en los scripts).**
- `scope` ya encuentra copias y gemelos. El script agrupa los sitios cuyo código normalizado coincide: el mismo AST tras quitar comentarios y espacios y tras igualar `async def`/`def` y `await`, o una similitud muy alta.
- **`specify`:** una decisión por grupo. La vista muestra el primer miembro completo y las diferencias de los demás, y la frase se aplica a todos.
- **`edit`:** el editor edita el primer miembro. El script traslada el cambio a los demás:
  - el mismo diff, aplicado con margen de contexto, con la correspondencia async/await para los gemelos;
  - después, la comprobación de sintaxis y los tests de siempre;
  - solo si el traslado no aplica o rompe algo, ese miembro recibe su propia llamada de `edit`, como hoy.

**Ahorro esperado.** En una tarea con un par de copias se ahorran:
- una decisión de `specify` (≈31 s);
- una llamada de `edit` completa: `open` 4,5 s + decisión ≈15 s + respuesta 4,4 s;
- ≈2 llamadas del coordinador (≈7 s).

En total, **≈60 s en esa tarea**. No sé con qué frecuencia aparecen copias o gemelos (el brief no lo cuantifica). Si una de cada tres tareas tiene un grupo, salen **≈20 s por tarea de media**, y más donde hay 2 grupos.

**Riesgo para las decisiones: bajo o medio.** El modelo decide lo mismo; solo deja de repetirlo.
- Riesgo de traslado: dos copias «casi iguales» pueden necesitar cambios distintos.
- Mitigación: agrupar solo con identidad normalizada, que el modelo vea las diferencias en la vista de `specify` y que la vuelta a la edición individual sea automática cuando el traslado falla.

**Esfuerzo:** medio (agrupado, traslado del diff y vuelta atrás en el script).

**Cómo medirlo.** En las tareas con copias o gemelos:
- llamadas de `specify` y `edit` por tarea;
- porcentaje de traslados aceptados y porcentaje que vuelve a edición individual;
- resueltas, y diferencias entre las copias en el parche final (deberían ser las mismas que en la referencia).

**Genérica:** sí. El criterio es la similitud del código, no nombres ni bibliotecas.

---

## 4. Instrucción de «pensar poco» en los agentes que no llegan al tope

**Técnica.** Reducir el pensamiento con la instrucción de sistema. La guía oficial de Gemma 4 describe una instrucción «LOW» que baja el pensamiento ≈20 %. TALE y *Chain of Draft* muestran recortes mayores con una pérdida pequeña cuando se indica brevedad o un presupuesto.

**Mecanismo.**
- `requirements`, `locate`, `scope`, `edit` y `review` piensan menos que su tope; su longitud la decide el modelo, no el tope. Se añade a su instrucción una línea fija:
  > «Piensa breve: unas pocas líneas de borrador bastan; el script comprueba tu respuesta y te dirá si falla.»

  La segunda mitad de la frase es cierta: el script valida, aplica y ejecuta tests. Saber que hay red reduce las comprobaciones repetidas.
- Los topes no cambian.
- Las vistas de 2.000–12.000 caracteres pueden recortarse a lo necesario para la decisión: una entrada más larga suele alargar también el pensamiento. Eso queda fuera de esta propuesta, como medida aparte.

**Ahorro esperado.**
- Tokens de decisión fuera de `specify`: 60.333 − ≈38.500 ≈ 21.800, de los que ≈85 % es pensamiento: ≈18.500.
- Un 20 % menos son 3.700 tokens, es decir 103 s: **≈10 s por tarea**.
- Con un recorte del 30–40 %, como en *Chain of Draft*, serían ≈15–20 s.

**Riesgo para las decisiones: bajo o medio.**
- `locate` y `scope` eligen entre opciones mostradas; ahí reflexionar menos rara vez cambia la elección.
- En `edit` el riesgo es mayor (código nuevo). Puede excluirse en la primera prueba.

**Esfuerzo:** pequeño.

**Cómo medirlo.**
- Tokens de pensamiento por rol, antes y después.
- `locate`: proporción de sitios elegidos que están en el parche de referencia.
- `edit`: ediciones deshechas por los tests o por sintaxis.
- Resueltas.

**Genérica:** sí.

---

## 5. Referenciar en vez de copiar en las salidas largas

**Técnica.** La salida visible cuesta lo mismo por token que el pensamiento. Lo que el script ya tiene no debe volver a escribirlo el modelo.

**Mecanismo.**
- **`requirements`.** Hoy el modelo cita literalmente las frases de la incidencia. Pasaría a citar solo un ancla de 5–8 palabras de cada frase; el script la amplía a la frase completa por coincidencia aproximada y la guarda. Se conserva el efecto de anclaje de citar (el modelo sigue apuntando a texto real), sin el coste de copiarlo.
- **`edit` en definiciones largas.** La forma «definición completa» obliga a reescribir líneas que no cambian. Para un sitio de más de ≈40 líneas, la vista pone primero la forma de rango (`[sitio, primera, última, líneas nuevas]`) sobre el código numerado, y deja la definición completa para los sitios cortos. Ambas formas ya existen; solo cambia cuál se recomienda según el tamaño.
- **Línea final y petición del coordinador:** cortas, como en la propuesta 2.

**Ahorro esperado.**
- Una definición de 80 líneas son ≈800 tokens de salida (≈22 s); un rango de 10 líneas, ≈150 (≈4 s). Si media tarea de cada dos tiene una edición larga, se ahorran ≈9 s por tarea.
- Citas de `requirements`: ≈150–300 tokens por tarea, es decir 4–8 s.
- Total: **≈8–15 s por tarea**.

**Riesgo para las decisiones: medio en `edit`.** Los modelos se equivocan con los números de línea, y para modelos débiles reescribir la función entera es más fiable (experiencia de los formatos de edición de Aider). Por eso se limita a definiciones largas, con las líneas numeradas a la vista y la validación del script. En `requirements` el riesgo es bajo.

**Esfuerzo:** pequeño (`requirements`) a medio (`edit`).

**Cómo medirlo.**
- Tokens de salida visible por rol.
- `requirements`: porcentaje de anclas que el script no resuelve.
- `edit`: rangos inválidos o ediciones deshechas, frente a la definición completa.

**Genérica:** sí.

---

## Complemento gratuito: topes de salida ajustados a cada rol

Esto no ahorra tiempo de media, pero limita lo que cuesta un fallo. Un bucle de repetición a temperatura 1,0 con un tope de 4.096 cuesta hasta 114 s, más de un tercio de la tarea.

Cada `max_output_tokens` debería ser el presupuesto de pensamiento más el doble de la respuesta más larga razonable:
- `locate`, `scope` y `review`: 512 + ≈300, es decir 1.024 en vez de 2.048;
- coordinador: 256;
- `edit` se queda en 4.096, porque necesita definiciones completas.

En vLLM ese límite incluye el pensamiento: nunca debe quedar por debajo de presupuesto + respuesta.

## Orden recomendado para probar

Primero el 2 y el 4, que son de riesgo bajo y esfuerzo pequeño: ≈38–47 s por tarea entre los dos. Después el 1 en dos pasos (640 y luego 512), midiendo la calidad de las frases. El 3 y el 5 a continuación, porque requieren cambios en los scripts.

En total, ≈110–140 s por tarea, sobre 264 s de modelo. Ese tiempo libera la mayoría de las tareas que hoy se cortan a 5 min. Los ahorros se solapan en parte: el 2 y el 5 comparten la línea corta.

## Ideas consideradas y descartadas

- **`include_thoughts: false`.** La guía oficial de Gemma 4 dice que los pensamientos se quitan entre turnos del usuario, pero **no deben quitarse entre llamadas a herramientas dentro de un mismo turno**. Cada sesión de un sub-agente, y la del coordinador, es un solo turno de llamadas a herramientas, así que `true` es lo correcto. Sí conviene verificar, en el prompt que llega a vLLM, que los pensamientos anteriores aparecen de verdad. Si la plantilla o LiteLLM los descartan, el especificador vuelve a razonar cada sitio desde cero; corregirlo sería un ahorro.
- **Bajar la temperatura para acortar el pensamiento.** No encontré evidencia sólida de que acorte el pensamiento. En modelos que razonan, la temperatura baja o voraz favorece bucles de repetición, que cuestan más tiempo. Las guías de los modelos de razonamiento piden sus valores por defecto.
- **Penalizaciones de presencia o frecuencia.** Castigan repetir identificadores y código, que es justo lo que una edición necesita.
- **Apagar el pensamiento en `specify` (resultados de *Reasoning models can be effective without thinking*, Ma et al. 2025).** Su ventaja aparece con muestreo paralelo y selección (pass@k), no con una sola respuesta. Es demasiado arriesgado en la decisión más difícil.
- **Secuencias de parada.** Las llamadas a herramientas se analizan con el formato de llamada de Gemma 4, y una secuencia de parada mal elegida corta el marcado de la llamada. La respuesta final ya termina sola tras la línea, así que no hay nada que ganar.
- **Bajar `max_output_tokens` para forzar menos pensamiento.** El tope de pensamiento ya lo hace; el de salida cortaría la respuesta, no el pensamiento.
- **`seed`.** No afecta a la longitud.
- **LoRA para pensar más corto.** Consiste en autodestilar las trazas correctas más cortas del propio modelo (permitido: son salidas de Gemma, no de otro LLM comercial). Es prometedor pero de esfuerzo grande: hace falta un banco de trazas juzgadas. Lo reconsideraría después del 1 y el 4.
- **Fuera de mi ángulo, pero lo señalo.** vLLM atiende una petición a la vez con el 2 % de la caché KV en uso. La decodificación de un modelo W4A16 está limitada por memoria, así que dos peticiones simultáneas darían casi el doble de tokens por segundo en total. Un `ParallelAgent` con K especificadores fijos, cada uno con el sitio *i* del plan si existe, aprovecharía eso. Lo frenan la escritura concurrente en el fichero de estado y el espacio de trabajo compartido; lo dejo para quien evalúe la concurrencia.

## Fuentes

- [Gemma 4: formato de prompts](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4): pensamiento por `<|think|>`, quitar pensamientos entre turnos pero no entre llamadas a herramientas, instrucción «LOW» ≈20 % y canal vacío en modelos grandes.
- [vLLM 0.19: salidas de razonamiento](https://docs.vllm.ai/en/v0.19.0/features/reasoning_outputs/): `thinking_token_budget` por petición con `--reasoning-config`, y corte del pensamiento al agotarse el presupuesto.
- Muennighoff et al. 2025, *s1: Simple test-time scaling* (*budget forcing*).
- Han et al. 2024, *Token-Budget-Aware LLM Reasoning* (TALE).
- Xu et al. 2025, *Chain of Draft*.
- Chen et al. 2024, *Do NOT Think That Much for 2+3=?*
- Ma et al. 2025, *Reasoning Models Can Be Effective Without Thinking*.
