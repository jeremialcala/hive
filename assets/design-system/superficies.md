# Superficies y arquitectura de información

Hive se entra por la **landing**, se opera desde el **área del cliente** (la PWA de autogestión del tenant) y se gobierna desde el **área del operador** (el portal interno, admin/root). La navegación mapea los dominios de la `hive-api` y separa con claridad la vista del gestor de bots de la del administrador del sistema. La vista previa *Superficies* en Componentes muestra los tres esqueletos lado a lado.

| | Landing | Área del cliente (tenant) | Área del operador (admin/root) |
|---|---|---|---|
| Quién | Empresa que evalúa Hive | Gestor de bots del tenant | Operador de la plataforma |
| Trabajo | Entender y empezar | Crear, desplegar y auditar bots | Administrar tenants, claves y el flujo de eventos |
| Cuerpo | `body-lg` | `body` | `body` |
| Ritmo | `space-7` / `space-8` | `space-3` / `space-4` | `space-2` / `space-3` |
| Tema | Claro u oscuro según el sistema | Oscuro por defecto | Oscuro por defecto |
| Navegación | Barra superior + CTA | Barra lateral `sidebar-w`; `TabBar` inferior en teléfono | Barra lateral `sidebar-w` con selector de cliente |
| Marca | `hive-amber` en CTA y celdas del hero | `hive-amber` solo en acción primaria y selección | `hive-amber` solo en acción primaria |

## Landing (entrada)

La landing vende control técnico. Abre con `heading-xl` sobre `slate-black` y un bloque de celdas hexagonales en `hive-amber`, `amber-soft` y `dark-ash`: el único sitio donde el motivo ocupa espacio grande.

- Estructura: hero → canales (tres `ChannelChip` grandes) → el flujo de un evento (un `CodeSnippet` real con el `GET /chat/{bot_id}` y la verificación HMAC) → el wizard en cuatro pasos (`Stepper` estático) → planes → CTA final → pie.
- Acción primaria `Button variant="primary"` ("Crear mi primer bot"); secundaria `variant="secondary"` ("Ver la API").
- Sin capturas de Meta ni logotipos de terceros. Nada se anima al cargar.

## Área del cliente (tenant)

Es donde el gestor de bots pasa el día. Instalable como PWA (`display: standalone`, `theme-color` = `slate-black`); en teléfono la barra lateral se convierte en `TabBar`.

1. **Visión general** (`/`): cuatro `MetricCard` con métricas de ingesta en tiempo real: mensajes procesados, latencia del motor de respuestas (`EventAction`, p95), estado de los endpoints y bots activos. Debajo, el visor de eventos recientes en `DataTable`.
2. **Bots** (`/bot`): lista de `BotCard` con estado REG/ACT/ERR, entorno, canales conectados (`ChannelChip`) y último sync. La ficha de un bot tiene `Tabs`: *Identidad* (nombre, rol, tono), *Credenciales de Meta* (`Field secret`: ofuscados, nunca visibles tras guardarse) y *Reglas e intents*.
3. **Cerebro e intents** (`/chat/{bot_id}/rules`): editor estructurado del System Prompt (`Field multiline mono`) y panel de herramientas que el LLM puede ejecutar. Editar un intent abre un `SlideOver`.
4. **Audiencia y contactos** (`/contacts`): buscador global por `waId` o `fbId` (`Field mono`), `DataTable` de contactos y, en `SlideOver`, el visor de vueltas (`Thread`) para auditoría. Flujo de privacidad con dos acciones prominentes: *Exportar datos* (`secondary`) y *Borrar definitivamente* (`danger` → `ConfirmDestructive`), que alimenta el registro de supresiones.
5. **Credenciales** (`/keys`): gestor de aserciones OAuth 2.0 y rotación de claves públicas del cliente. `DataTable` con `Badge tone="success"` en la clave activa, `CodeSnippet` con la aserción de prueba y `ConfirmDestructive` al revocar.

Onboarding de un bot en cuatro pasos (`Stepper` + `GoLive`): *Identidad* (`name`, `role`, `description`, `tone`) → *Motor de reglas* (reglas iniciales e intents clave) → *Cifrado y credenciales* (`facebookToken`, `whatsappToken`, `appSecret` en `Field secret`, con la nota "viajan cifrados y se almacenan en Andvari") → *Go-Live* (la UI muestra `verifyToken` y la URL `https://hive-webhook.higerotech.com/chat/{bot_id}` en `CodeSnippet`, y `GoLive` queda escuchando hasta el primer `GET /chat/{bot_id}` exitoso desde Meta; entonces el bot pasa a ACT).

## Área del operador (admin/root)

Denso y de bajo nivel. Barra lateral con selector de cliente arriba (nombre + `key` con id) y tres secciones:

1. **Clientes** (`/clients`): alta, baja y auditoría de tenants en `DataTable`; la baja pasa por `ConfirmDestructive` con el UUID del cliente.
2. **Service keys**: asignación de claves del motor (`hive-coordinator`, `hive-responder`) a clientes concretos; cada asignación muestra su `CodeSnippet` de verificación.
3. **Trazas y eventos** (`/events`): visor de bajo nivel del flujo de Bifröst y de las validaciones de webhook. Filas `row-dense`, columnas en `key`, payload expandible en `code`; `LoadingState` con micro-copy del bus mientras llega la cola.

Reglas del área: `hive-amber` solo en el botón primario y la fila seleccionada; `Badge` hace el trabajo visual; sin sombras salvo `SlideOver` y menús; el escalado de segundo nivel reutiliza `Thread` con el contacto enmascarado (`waId 5841…4421`) y un botón "Mostrar" que queda en auditoría.
