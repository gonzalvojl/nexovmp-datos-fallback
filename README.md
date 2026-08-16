# NexoVMP · servidor secundario

Mirror público de solo lectura para dar redundancia al catálogo y a la normativa municipal de NexoVMP.

- Primario: Cloudflare Pages (`nexovmp-datos.pages.dev`)
- Secundario: GitHub Pages
- La app valida esquema y SHA-256 antes de aceptar datos.
- Si una actualización del primario falla o no valida, este workflow falla y el último despliegue válido de GitHub Pages permanece publicado.

## Activación

1. Crear un repositorio **público** llamado `nexovmp-datos-fallback`.
2. Subir todo el contenido de este paquete a la raíz del repositorio.
3. En **Settings → Pages → Build and deployment → Source**, elegir **GitHub Actions**.
4. Ir a **Actions → NexoVMP · Servidor secundario → Run workflow**.
5. El sitio quedará en `https://<usuario>.github.io/nexovmp-datos-fallback/`.

No contiene el scraper DGT ni secretos del repositorio privado; únicamente el workflow que replica los JSON públicos ya publicados por NexoVMP.
