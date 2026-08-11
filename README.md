# Zaragoza Vivienda Bot 🏠

Monitor gratuito para vigilar fuentes oficiales sobre vivienda/alquiler asequible en Zaragoza y enviar avisos a un canal de Discord mediante un webhook.

## Qué vigila

Actualmente consulta cada hora:

- Ayuntamiento de Zaragoza — noticia de las 608 viviendas de Actur/Valdespartera.
- Zaragoza Vivienda.
- Plataforma Alquiler Asequible Zaragoza.
- Portal municipal de Vivienda Joven.
- Suelo y Vivienda de Aragón.

La lista está en `monitor.py`, dentro de `SOURCES`, y se puede ampliar.

## Cómo funciona

1. GitHub Actions ejecuta `monitor.py` una vez por hora.
2. El script descarga las páginas y extrae texto visible.
3. Compara cada página con la versión de la ejecución anterior.
4. Si detecta un cambio relevante, manda un embed a Discord.
5. Palabras especialmente críticas como "plazo de solicitudes", "abierto el plazo" o "presentar solicitud" generan un aviso rojo.
6. La primera ejecución solo crea la referencia inicial y **no manda alertas falsas**.

## Coste

Pensado para un **repositorio público** usando un runner estándar `ubuntu-latest`.

A fecha de creación de este proyecto, GitHub documenta que los runners estándar alojados por GitHub son gratuitos e ilimitados para repositorios públicos. No se usa servidor, base de datos ni API de pago.

Importante: GitHub también documenta que los workflows programados de repositorios públicos pueden desactivarse tras 60 días sin actividad. El workflow incluido crea un pequeño "heartbeat" como máximo una vez cada 30 días para mantener actividad en el repositorio.

No existe una forma de garantizar que las políticas de GitHub no cambien en el futuro; por eso conviene revisar sus condiciones si algún día aparece un aviso de facturación.

## Instalación

### 1. Crear un repositorio público

En GitHub crea un repositorio nuevo, por ejemplo:

`zaragoza-vivienda-bot`

Debe ser **Public** si quieres aprovechar el uso gratuito de runners estándar que GitHub ofrece actualmente a repositorios públicos.

### 2. Subir estos archivos

Sube todo el contenido de esta carpeta al repositorio, incluyendo la carpeta oculta:

`.github/workflows/monitor.yml`

### 3. Guardar la URL del webhook como Secret

No pongas la URL del webhook en el código.

En tu repositorio:

**Settings → Secrets and variables → Actions → New repository secret**

Nombre:

`DISCORD_WEBHOOK_URL`

Valor:

la URL completa de tu webhook de Discord.

### 4. Dar permisos de escritura al workflow

El workflow necesita guardar `state.json` y el heartbeat.

En GitHub:

**Settings → Actions → General → Workflow permissions**

Selecciona:

**Read and write permissions**

y guarda.

### 5. Ejecutar una prueba de Discord

Ve a:

**Actions → Monitor vivienda Zaragoza → Run workflow**

Marca:

**Enviar solo un mensaje de prueba a Discord**

y ejecuta.

Debería aparecer un mensaje:

`✅ Monitor conectado`

en vuestro canal de Discord.

### 6. Inicializar el monitor

Vuelve a **Run workflow**, esta vez sin marcar la prueba.

La primera ejecución descargará las fuentes y rellenará `state.json`, pero no enviará avisos por cambios porque todavía no existe una versión anterior con la que comparar.

A partir de entonces el cron se ejecuta automáticamente una vez por hora.

## Horario

El cron es:

```yaml
17 * * * *
```

Es decir, aproximadamente en el minuto 17 de cada hora. Se ha elegido un minuto distinto de `00` porque GitHub advierte de que los workflows programados pueden retrasarse cuando hay mucha carga, especialmente al comienzo de cada hora.

GitHub no garantiza ejecución al segundo exacto: para este proyecto eso no importa, porque el objetivo es detectar novedades con una demora aproximada máxima de una hora.

## Nombre del bot en Discord

El webhook usa:

`Zaragoza Vivienda Bot`

Se puede cambiar en `monitor.py`, en el campo:

```python
"username": "Zaragoza Vivienda Bot"
```

También puedes configurar nombre y avatar desde Discord al editar el webhook.

## Añadir otra web

Añade un elemento a `SOURCES`:

```python
{
    "name": "Mi nueva fuente",
    "url": "https://ejemplo.es/pagina",
    "always_notify": False,
},
```

`always_notify: True` significa que cualquier cambio de texto de esa página generará aviso.

Con `False`, solo avisará cuando el texto nuevo incluya alguna palabra de `KEYWORDS`.

## Seguridad

- Nunca publiques `DISCORD_WEBHOOK_URL`.
- Si la URL del webhook se filtra, elimínalo/regénéralo inmediatamente desde Discord.
- El archivo `state.json` no guarda ningún secreto.
- El repositorio puede ser público sin revelar el webhook, siempre que este esté en GitHub Secrets.

## Nota sobre scraping

El monitor hace una petición por página cada hora, un volumen muy bajo. Aun así, si una web cambia su HTML, añade protección antibot o cambia de URL, puede ser necesario adaptar el parser.

Las fuentes y palabras clave están deliberadamente separadas al principio de `monitor.py` para facilitar ese mantenimiento.
