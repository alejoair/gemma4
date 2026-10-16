# Propuestas C: descargar decisiones en código determinista

Ángulo: qué decisiones o pasos del modelo puede tomar, acotar o preparar el código (análisis estático, ranking,
heurísticas, precálculo, ejecución), para que el 31B decida menos veces o sobre opciones más pequeñas, y conserve solo
las decisiones que hace bien (entender el issue, elegir el sitio, decidir qué cambia, escribir el código).

## Cuentas de partida (del brief, por tarea, media de 10)

- Tiempo de modelo: 2.635 s / 10 = **263,5 s por tarea** (el límite es 300 s; 7 de 10 llegaron al límite).
- 27 llamadas y 8.600 tokens generados por tarea; a 36 tok/s eso son 239 s: **el tiempo es generación**, y el 85 % es
  pensamiento. Una decisión difícil gasta todo su presupuesto: su tiempo lo fija el presupuesto, no la dificultad.
  Por eso lo que ahorra tiempo es **quitar llamadas enteras**, no hacer más fácil una llamada que igual agota el
  presupuesto.
- Coste fijo de cada invocación de un subagente, sin decidir nada: `open` 227/41 = 5,5 s + respuesta final
  235/54 = 4,4 s + llamada del coordinador 276/80 = 3,5 s = **13,4 s por invocación**. Con unas 5,4 invocaciones por
  tarea, son ~73 s por tarea (28 %) de llamadas cuyo contenido ya decidió el script.
- Coste de un sitio del plan: especificar ~20-30 s (una decisión de specify) + editar ~26 s (edit: 229 s / ~9 sitios
  editados + 3,5 s del coordinador) ≈ **50 s por sitio**. Specify (124 s/tarea, 47 %) se come el tiempo antes de que
  lleguen las ediciones: solo ~9 sitios editados en 10 tareas.

Los ahorros de las propuestas no se suman tal cual (varias quitan las mismas llamadas); la estimación conjunta está al
final.

---

## P1. Alcance determinista y perezoso: el script decide qué sitios relacionados entran, y cuándo

**Técnica.** Análisis de impacto de cambios (change impact analysis) incremental, como en CodePlan (Bairi et al.,
FSE 2024: después de cada edición, un análisis de dependencias "may-impact" decide la siguiente obligación de edición,
en lugar de que el LLM planifique todo de antemano).

**Mecanismo.**
1. Se elimina la etapa **scope** como decisión del modelo. El script clasifica las piezas relacionadas que hoy lista:
   - *Obligatorias* (entran sin preguntar): clones del sitio elegido (mismo nombre y AST normalizado con similitud de
     tokens ≥ 0,8, p. ej. la misma función en dos paquetes) y gemelos async/sync (misma forma tras quitar
     `async`/`await`/`a`-prefijos). Ver P2 para que no cuesten llamadas.
   - *Contexto* (no entran en el plan): llamadores, redefiniciones, exportaciones. Se muestran en la vista del
     especificador como "código relacionado, sin cambios salvo que haga falta", como ya se muestra el código que llama.
2. Orden "primero lo principal": el plan arranca con el sitio que el modelo eligió en locate (el de mayor rango si
   eligió varios). Se especifica y se edita ese sitio, y **después** el script calcula el impacto de la edición real:
   - firma cambiada (parámetro nuevo, renombrado, eliminado, valor por defecto) → llamadores que no la cumplen
     (llamadas con argumentos posicionales o con nombre que ya no encajan, `**kwargs` que reenvían);
   - nombre nuevo usado y no importado/exportado (`__all__`, `__init__`) → el módulo que debe exportarlo;
   - clase de excepción nueva o cambiada → sitios que la capturan;
   - tests relacionados que fallan tras la edición (ya se ejecutan) → las funciones de sus trazas dentro del repo.
   Solo esos sitios entran en el plan, con el motivo como frase ya escrita ("`f` ahora exige `timeout`; esta llamada
   no lo pasa"). El especificador conserva su forma actual de añadir un sitio.
3. El especificador recibe el plan de uno en uno (o el principal y sus clones), no 2-6 sitios de golpe.

**Ahorro esperado.**
- Etapa scope: 210 s / 10 = 21 s por tarea + ~6 llamadas de coordinador / 10 × 3,5 s ≈ **23 s por tarea**.
- Sitios no especificados: si specify hace hoy ~4-5 decisiones por tarea y el alcance perezoso quita ~1 sitio
  "por si acaso" por tarea (llamadores y redefiniciones que acaban sin cambio), son ~25 s de specify más, cuando se
  habría llegado a editarlo, ~26 s de edit: **25-50 s por tarea**.
- Total: **≈ 50-70 s por tarea** (19-27 % de los 263 s). Además, con el orden "primero lo principal" la primera edición
  se aplica antes, así que una tarea que llega a los 5 min tiene parche con más frecuencia (hoy 6 de 10 con parche).

**Riesgo para la calidad.** Medio-bajo. Las correcciones en varios sitios que no se derivan de una firma, una
exportación o un test (p. ej. dos funciones que deben cambiar por la misma regla sin llamarse entre sí) dependen de que
el especificador las pida; hoy las pide scope. Mitigación: los candidatos de contexto siguen visibles para el
especificador, que puede añadirlos con la forma que ya existe. Las decisiones que quedan son más pequeñas: el modelo ya
no elige entre 2-6 piezas listadas, solo dice si falta alguna. El modelo se equivoca más eligiendo "qué más cambia" de
una lista larga que el código detectando una firma rota.

**Esfuerzo.** Medio (el script ya tiene el grafo de llamadas, el mapa test→función y la detección de copias y
gemelos; falta el diff de firmas y exportaciones y el bucle "editar → impacto").

**Cómo medirlo.**
- Sin modelo, en las 129 tareas de desarrollo: exhaustividad del plan frente a las funciones que toca el parche de
  referencia (plan actual = elegidos + scope; plan nuevo = elegidos + clones + impacto del parche de referencia
  aplicado). Se ve cuántos sitios necesarios se pierden y cuántos sobran.
- Con modelo (la única prueba válida): mismas 10 tareas; comparar sitios especificados por tarea, decisiones de
  specify, tiempo hasta la primera edición aplicada, tareas con parche no vacío a los 5 min, tareas resueltas según
  `judge`.

**Genérico.** Sí: firmas, imports, excepciones y clones son propiedades de cualquier código Python; ninguna regla
nombra un repositorio ni un issue.

---

## P2. Propagación de ediciones a clones y gemelos: se especifica y se edita una vez

**Técnica.** Ediciones sistemáticas a partir de un ejemplo (Sydit, Meng, Kim y McKinley, PLDI 2011; LASE, ICSE 2013)
y la transformación determinista async→sync de `unasync` (la usa httpcore para mantener su código sync generado desde
el async).

**Mecanismo.**
1. El script agrupa los sitios del plan en clases de clones (AST normalizado: nombres de variables locales
   abstraídos; gemelos async/sync con la tabla de reescritura de `unasync`: `async def`→`def`, `await x`→`x`,
   `__aenter__`→`__enter__`, `Async`-prefijo→sin prefijo, `aclose`→`close`, etc.).
2. Se especifica y se edita solo el representante (el que eligió el modelo, o el primero).
3. Tras aplicar la edición, el script trasplanta el diff a cada clon: alinea las líneas del representante original con
   las del clon (difflib), aplica los mismos trozos traduciendo los tokens según la tabla de diferencias entre ambos
   (nombres que difieren, async/sync), comprueba la sintaxis y ejecuta los tests relacionados del clon, como hace hoy
   con cualquier edición.
4. Si el trasplante no aplica limpio o rompe tests, el clon vuelve al plan normal (especificar + editar con el
   modelo), con el diff del representante en su vista como ejemplo. El modelo nunca recibe una decisión peor que hoy.

**Ahorro esperado.** Cada clon resuelto por código ahorra su decisión de specify (~25 s) y su invocación de edit
(~26 s): **≈ 50 s por clon**. El brief dice que las copias en dos paquetes multiplican las llamadas; si la media es
0,3-0,5 clones por tarea (httpx y sus gemelos async/sync hacen que en algunos repositorios sea más), son
**15-25 s por tarea de media y 50-100 s en las tareas que los tienen**. Es justo en esas tareas donde hoy se llega a los
5 min con sitios especificados sin editar.

**Riesgo para la calidad.** Bajo. La decisión difícil (qué cambia) la toma el modelo una vez; repetirla en un clon no
aporta información. Además, el código no se equivoca de forma distinta en cada copia, mientras que el modelo a
temperatura 1,0 puede escribir dos versiones divergentes del mismo cambio. Riesgo real: clones que deben cambiar de
forma distinta; el fallo de los tests del clon y la vuelta al modelo lo cubren en parte.

**Esfuerzo.** Medio (la detección de clones ya existe; falta el trasplante y la tabla async/sync).

**Cómo medirlo.** Sin modelo: en las tareas de desarrollo cuyo parche de referencia toca dos clones, aplicar el trozo
del representante y trasplantarlo; contar cuántos coinciden con la referencia (o pasan los tests). Con modelo:
decisiones de specify y de edit por tarea, trasplantes aceptados / devueltos al modelo, resueltos según `judge`.

**Genérico.** Sí: clones y pares async/sync aparecen en cualquier biblioteca; la tabla de `unasync` es la convención
del lenguaje, no de un proyecto.

---

## P3. Enrutar sin el coordinador: el siguiente paso lo ejecuta el propio subagente

**Técnica.** El enrutamiento ya lo decide el script; el coordinador solo copia su línea "next call" (80 llamadas,
276 s). Se quita el paso de modelo que copia una decisión ya tomada.

**Mecanismo (dos variantes, de mayor a menor ahorro).**
- **A. Transferencia en cadena.** Los agentes de etapa pasan de `agent_tool` a `sub_agents` del agente raíz; ADK
  añade la herramienta `transfer_to_agent`. La última respuesta del script en cada etapa dice "NEXT:
  transfer_to_agent(agent_name='specify_fix')" (o `submit_patch` al final), y el subagente hace esa llamada **en lugar
  de** su respuesta final de una línea. Cada agente de etapa lleva `include_contents: none`, para que empiece sin el
  historial de las etapas anteriores (el contexto limpio actual) y solo vea la transferencia. Las vueltas atrás y las
  etapas omitidas siguen decididas por el script: simplemente nombra otro agente.
  Ahorro: desaparecen las llamadas del coordinador menos la primera; la llamada de transferencia sustituye a la
  respuesta final (coste parecido, ~114 tokens). **≈ 24 s por tarea** (276 s / 10 − 1 llamada inicial de 3,5 s).
  Hay que verificar en el ADK del arnés: que `include_contents` se acepta en la configuración, qué ve un agente
  transferido con él, y cómo trata el arnés un agente que termina tras una transferencia (el empujón si falta
  `submit_patch`). La etapa edit, que hoy hace un sitio por invocación, necesitaría transferirse a sí misma o a una
  segunda copia del agente editor para el siguiente sitio; esto también hay que probarlo.
- **B. Si A no funciona en el arnés.** Mantener el coordinador pero tratarlo como lo que es, un copista: presupuesto de
  pensamiento mínimo (p. ej. 32-64) y `max_output_tokens` ~200. Hoy gasta 118 tokens por llamada; si la mitad es
  pensamiento que no decide nada, son ~60 tokens × 8 llamadas = 480 tokens ≈ **13 s por tarea**, con esfuerzo
  pequeño.

**Riesgo para la calidad.** Muy bajo en B (el coordinador no decide). En A, bajo para las decisiones (las mismas
vistas, el mismo script), pero hay un riesgo de sistema: un subagente que no llama a la transferencia termina la
ejecución sin pasar por el coordinador. La línea "NEXT" ya es la llamada exacta, y en la última ejecución solo 4
de 270 llamadas fueron rechazadas, erróneas o no usadas.

**Esfuerzo.** A: medio (reestructurar la configuración y verificar el comportamiento de ADK). B: pequeño.

**Cómo medirlo.** Llamadas por tarea por tipo (coordinador, respuesta final / transferencia, `open`) y su tiempo;
tareas que acaban sin `submit_patch`; llamadas mal formadas; resueltos.

**Genérico.** Sí: no toca el contenido de ninguna decisión.

---

## P4. Requisitos por selección, no por transcripción, y unidos a locate

**Técnica.** Precalcular la estructura del enunciado (segmentación, extracción de identificadores, código, trazas)
para que el modelo elija en vez de copiar. Copiar es generación pura: a 36 tok/s, 200 tokens citados son 5,5 s.

**Mecanismo.**
1. El script segmenta el enunciado en frases numeradas y bloques (código, salida esperada/obtenida, trazas) y marca
   las candidatas a requisito con señales léxicas genéricas ("should", "expected", "instead", "raises", "but",
   comparaciones de salida, bloques de traza). También extrae los identificadores del enunciado que existen en el
   repositorio (coincidencia exacta con definiciones) y los sube en el ranking BM25 de locate.
2. Una sola invocación hace requisitos y locate: la vista muestra las frases numeradas (con las marcadas
   preseleccionadas) y, debajo, los candidatos de locate. La decisión del modelo es:
   `{"requisitos": [[3, "lo que debe pasar"], [5, "..."]], "sitios": {"3": ["Clase.metodo"], ...}}`. El script
   reconstruye la cita literal a partir del número (en las vistas siguientes se sigue mostrando el texto, nunca un id
   suelto, para no perder el sentido tras la compactación).

**Ahorro esperado.** Una invocación menos por tarea: 13,4 s de coste fijo. La transcripción de las citas
(~150-300 tokens) pasa a números: 4-8 s. **≈ 15-20 s por tarea** (requisitos hoy: 339 s / 10 = 34 s por tarea).

**Riesgo para la calidad.** Bajo. Elegir requisitos y sitios juntos acerca la decisión de dónde a la de qué (el
modelo ve las dos cosas a la vez). El riesgo es una vista más larga (frases + candidatos, ~8-10 mil caracteres) en una
llamada con presupuesto 512; habría que darle a esa invocación el presupuesto de requisitos (1.024).

**Esfuerzo.** Pequeño-medio.

**Cómo medirlo.** Tiempo de las etapas requisitos + locate por tarea; requisitos con cita válida; exhaustividad de
locate frente a las funciones del parche de referencia (sin modelo en las 129 tareas, con modelo en las 10).

**Genérico.** Sí: segmentar texto y cruzar identificadores con las definiciones del repositorio vale para cualquier
issue.

---

## P5. Ejecutar el ejemplo del enunciado: localización y revisión sin modelo

**Técnica.** Reproducción determinista y localización por traza (AutoCodeRover y Agentless usan las trazas del issue
y pruebas de reproducción; aquí la reproducción no la escribe el modelo, se ejecuta lo que el enunciado ya trae).

**Mecanismo.**
1. Al inicio, el script extrae del enunciado los bloques de código ejecutables (los que importan el paquete del
   repositorio) y los ejecuta en el sandbox con el código original (límite ~10 s cada uno, sin red). Guarda la salida,
   la excepción y los marcos de la traza que caen dentro del repositorio. Si el enunciado trae una traza pegada, la
   analiza igual.
2. Localización: las funciones de esos marcos suben al principio del ranking de locate marcadas como "aparece en la
   traza del ejemplo" (el modelo sigue eligiendo).
3. Verificación tras editar: el script vuelve a ejecutar el ejemplo. Si antes fallaba con una excepción y ahora no, o
   la salida coincide con la "salida esperada" del enunciado, la revisión del requisito ligado a ese ejemplo se da por
   hecha sin llamar al modelo. Si nada cambia, el script vuelve directamente a specify/locate con la salida nueva en la
   vista, sin la decisión de review.
4. Sustituye a la etapa reproduce (hoy omitida por tiempo) con cero tokens.

**Ahorro esperado.** Modesto en tiempo directo: review 67 s / 10 = 6,7 s por tarea (más su llamada de coordinador),
y las vueltas atrás detectadas antes. Si los marcos de la traza son inequívocos, la decisión de locate es más rápida o
correcta a la primera, lo que evita un ciclo specify+edit sobre un sitio equivocado (~50 s cuando ocurre).
**≈ 10-20 s por tarea**, con la ganancia principal en calidad (un sitio equivocado es la forma más cara de gastar los
5 min).

**Riesgo para la calidad.** Bajo: es evidencia añadida, no una decisión quitada al modelo, salvo en la revisión, y
solo cuando el ejemplo pasa de fallar a funcionar. Riesgos: ejemplos que no se pueden ejecutar (red, ficheros,
servidores); el script solo usa los que terminan dentro del límite y lo dice.

**Esfuerzo.** Medio (extracción de bloques, ejecución aislada con límite, análisis de trazas).

**Cómo medirlo.** Sin modelo, en las 129 tareas: en cuántas el ejemplo se ejecuta y reproduce el fallo, y en cuántas
algún marco de la traza es una función del parche de referencia. Con modelo: llamadas a review, vueltas atrás,
locate acertado a la primera, resueltos.

**Genérico.** Sí: ejecutar el código del propio enunciado y leer su traza no depende del repositorio.

---

## Estimación conjunta

P1 (50-70 s) + P2 (15-25 s, parte ya contada en P1) + P3 A (24 s) + P4 (15-20 s) + P5 (10-20 s), descontando
solapes: **≈ 90-120 s por tarea** sobre 263 s. Bajaría el tiempo medio de modelo a ~150-170 s, dentro de los 300 s con
margen, y la diferencia se dedica a lo que hoy no llega: editar los sitios ya especificados. Orden sugerido para
probarlo: P3 B (pequeño, inmediato), P1, P2, P4, P3 A, P5.

## Ideas consideradas y descartadas

- **Presupuesto de specify según una dificultad estimada por el código** (dos agentes especificadores, 512 y 1.024,
  y el script elige): la dificultad de una corrección no se puede predecir con rasgos del código, y el presupuesto es
  justo la palanca de calidad de la decisión que solo el modelo sabe tomar.
- **Aceptar sin el modelo el primer candidato de BM25**: la precisión del primer puesto no basta; elegir el sitio es
  una decisión del modelo (P5 la ayuda con evidencia, no la sustituye).
- **Plantillas de edición para tipos de corrección frecuentes**: no son genéricas (sirven a una familia de issues) y
  reintroducen reglas por caso.
- **Quitar la llamada `open` pasando la vista en la petición del coordinador**: el coordinador tendría que copiar
  2.000-12.000 caracteres (hasta ~3.000 tokens, ~80 s).
- **Quitar `open` dejando que el siguiente agente vea en el historial la última respuesta del script de la etapa
  anterior** (`include_contents` por defecto): rompe el contexto limpio por agente y acerca la compactación de los
  14.336 tokens.
- **Precalcular o cachear ranking y tests**: el trabajo de los scripts es menos del 10 % del tiempo.
- **`ParallelAgent` para especificar o editar sitios a la vez**: vLLM sirve una petición a la vez; no hay ganancia.
- **Reproducción escrita por el modelo**: cuesta una decisión completa; P5 obtiene buena parte de su valor con cero
  tokens.
- **Secuencias de parada para cortar la respuesta final**: pueden cortar también la llamada a herramienta; el ahorro
  real lo da P3.
- **LoRA para pensar menos**: fuera de este ángulo (no descarga decisiones en código) y requiere datos de
  entrenamiento que respeten la regla.
