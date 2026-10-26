// Hive — tipos de los componentes de window.Hive (documentación; no se compilan). Exportado del design system, v5 2026-10-26.
import type { ReactNode, ChangeEvent } from "react";

export type Tone = "neutral" | "amber" | "success" | "warn" | "error" | "info" | "muted";
/** Estados del bot tal como los devuelve la hive-api. REV = cliente o clave revocados. */
export type BotState = "REG" | "ACT" | "ERR" | "REV";
export type Channel = "messenger" | "whatsapp" | "instagram";
export type IconName = "thumbs-up" | "thumbs-down" | "triangle-alert" | "shield-alert" | "home" | "message-circle" | "bot" | "sliders" | "users" | "key" | "plus" | "check" | "x" | "alert-circle" | "arrow-right" | "copy" | "lock" | "trash" | "activity";

export interface ButtonProps {
  /** primary = hive-amber (una por vista); secondary = borde; ghost = sin fondo (acciones por fila); danger = relleno error-red; danger-outline = destructiva secundaria. */
  variant?: "primary" | "secondary" | "ghost" | "danger" | "danger-outline";
  /** sm = 32px (tablas), md = 36px, lg = 44px (móvil y landing). */
  size?: "sm" | "md" | "lg";
  icon?: IconName;
  disabled?: boolean;
  type?: "button" | "submit";
  ariaLabel?: string;
  onClick?: () => void;
  children?: ReactNode;
}

export interface FieldProps {
  id?: string;
  label?: string;
  placeholder?: string;
  value?: string;
  defaultValue?: string;
  onChange?: (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  help?: string;
  /** Qué pasó y qué hacer; marca el campo con error-red y sustituye a help. */
  error?: string;
  required?: boolean;
  disabled?: boolean;
  type?: string;
  multiline?: boolean;
  rows?: number;
  /** JetBrains Mono: tokens, ids, URLs, System Prompt. */
  mono?: boolean;
  /** Credencial (facebookToken, whatsappToken, appSecret, clave privada): tipo password y candado. */
  secret?: boolean;
  /** Con secret: ya guardado en Andvari; el campo se ofusca y no vuelve a mostrar el valor. */
  saved?: boolean;
}

export interface BadgeProps {
  tone?: Tone;
  /** Estado del bot; fija tono, código y palabra ("ACT · Activo"). */
  state?: BotState;
  /** Con state: solo el código, sin la palabra (tablas densas). */
  short?: boolean;
  dot?: boolean;
  children?: ReactNode;
}

export interface ChannelChipProps {
  /** messenger e instagram pintan meta-blue; whatsapp pinta whatsapp-green. */
  channel: Channel;
  size?: "md" | "lg";
  /** Canal no conectado al bot: borde discontinuo y sin color. */
  disconnected?: boolean;
  /** false oculta el nombre y deja la inicial. */
  label?: boolean;
  children?: ReactNode;
}

export interface CellProps {
  name: string;
  src?: string;
  /** Lado en px: 24 tablas, 36 listas, 56 cabeceras. */
  size?: number | string;
  tone?: "amber" | "ash" | "soft" | "error";
}

export interface MetricCardProps {
  label: string;
  /** Cifra ya formateada (`1 284`, `312`). */
  value: ReactNode;
  unit?: string;
  delta?: number;
  deltaUnit?: string;
  /** Invierte el color del delta cuando subir es malo (errores, latencia). */
  invert?: boolean;
  hint?: string;
  dense?: boolean;
}

export interface BotCardProps {
  name: string;
  state: BotState;
  /** Entorno: prod, staging, dev. */
  env?: string;
  channels?: Channel[];
  /** UUID del bot, mostrado en key. */
  botId?: string;
  /** Último sync, ya formateado ("hace 2 min", "14:02:17"). */
  lastSync?: string;
  selected?: boolean;
  onClick?: () => void;
}

export interface DataTableColumn<Row = Record<string, unknown>> {
  key: string;
  label: string;
  mono?: boolean;
  align?: "left" | "right";
  width?: string;
  render?: (row: Row) => ReactNode;
}
export interface DataTableProps<Row = Record<string, unknown>> {
  columns: DataTableColumn<Row>[];
  /** Una fila con `error: true` se tiñe de error-soft. */
  rows: (Row & { id?: string; error?: boolean })[];
  selectedId?: string;
  emptyText?: string;
  onRowClick?: (row: Row) => void;
}

export interface CodeVariant { lang: "bash" | "python" | "json" | "text"; label?: string; code: string; }
export interface CodeSnippetProps {
  /** Nombre del recurso o del comando, en mono, a la izquierda de la barra. */
  title?: string;
  /** Un solo bloque. */
  code?: string;
  lang?: CodeVariant["lang"];
  /** Varias pestañas (cURL, Python…). */
  variants?: CodeVariant[];
  onCopy?: (v: CodeVariant) => void;
}

export interface ConfirmDestructiveProps {
  title: string;
  /** Texto exacto (nombre o UUID) que el usuario debe escribir. */
  expected: string;
  fieldLabel?: string;
  facts?: { label: string; value: string }[];
  confirmLabel?: string;
  onConfirm?: () => void;
  onCancel?: () => void;
  /** Renderiza dentro del flujo (previews, documentación) en lugar de fijo sobre un scrim. */
  inline?: boolean;
  children?: ReactNode;
}

export interface LoadingStateProps {
  /** spinner (operación en curso) | rows (tabla) | cards (lista de tarjetas). */
  kind?: "spinner" | "rows" | "cards";
  /** Micro-copy técnico: qué se espera y de quién ("Esperando confirmación del bus de eventos…"). */
  message?: string;
  /** Detalle en mono: cola, stream o endpoint. */
  detail?: string;
  rows?: number;
}

export interface SlideOverProps {
  title: string;
  /** Id o ruta del recurso, en mono bajo el título. */
  subtitle?: string;
  onClose?: () => void;
  footer?: ReactNode;
  /** Renderiza dentro del flujo en lugar de fijo a la derecha. */
  inline?: boolean;
  children?: ReactNode;
}

export interface StepperProps {
  /** Por defecto: Identidad, Motor de reglas, Cifrado y credenciales, Go-Live. */
  steps?: string[];
  /** Índice del paso en curso (0–3). */
  current?: number;
}

export interface GoLiveProps {
  botId?: string;
  webhookUrl?: string;
  verifyToken?: string;
  /** true cuando llegó el primer GET /chat/{bot_id} exitoso desde Meta. */
  live?: boolean;
  /** Timestamp de la última llamada, en mono. */
  lastSeen?: string;
  children?: ReactNode;
}

export interface ThreadMessage {
  id: string;
  role: "customer" | "bot" | "operator";
  text: string;
  time?: string;
  /** Contacto enmascarado (waId 5841…4421) o nombre del operador. */
  author?: string;
  /** UUID del EventAction, en mono. */
  eventId?: string;
  flagged?: boolean;
  rating?: "up" | "down";
}
export interface ThreadProps {
  messages: ThreadMessage[];
  ratable?: boolean;
  onRate?: (message: ThreadMessage, rating: "up" | "down") => void;
  ariaLabel?: string;
}

export interface GuardrailDimension { name: string; score: number; }
export interface GuardrailScoreProps { title?: string; threshold?: number; dimensions: GuardrailDimension[]; }

export interface ApprovalBannerProps {
  title?: string;
  level?: "warn" | "danger";
  facts?: { label: string; value: string }[];
  requestedBy?: string;
  approveLabel?: string;
  onApprove?: (reason: string) => void;
  onReject?: () => void;
  children?: ReactNode;
}

export interface TabItem { value: string; label: string; count?: number; }
export interface TabsProps { items: TabItem[]; value?: string; defaultValue?: string; compact?: boolean; onChange?: (value: string) => void; }

export interface TabBarItem { value: string; label: string; icon: IconName; count?: number; }
export interface TabBarProps {
  /** Por defecto: Visión general, Bots, Contactos, Credenciales. */
  items?: TabBarItem[];
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
}

export interface EmptyStateProps { title: string; actionLabel?: string; onAction?: () => void; secondaryLabel?: string; onSecondary?: () => void; children?: ReactNode; }
export interface IconProps { name: IconName; size?: number; className?: string; }

declare global {
  interface Window {
    Hive: {
      Icon: (p: IconProps) => JSX.Element;
      Button: (p: ButtonProps) => JSX.Element;
      Field: (p: FieldProps) => JSX.Element;
      Badge: (p: BadgeProps) => JSX.Element;
      ChannelChip: (p: ChannelChipProps) => JSX.Element;
      Cell: (p: CellProps) => JSX.Element;
      MetricCard: (p: MetricCardProps) => JSX.Element;
      BotCard: (p: BotCardProps) => JSX.Element;
      DataTable: <Row>(p: DataTableProps<Row>) => JSX.Element;
      CodeSnippet: (p: CodeSnippetProps) => JSX.Element;
      ConfirmDestructive: (p: ConfirmDestructiveProps) => JSX.Element;
      LoadingState: (p: LoadingStateProps) => JSX.Element;
      SlideOver: (p: SlideOverProps) => JSX.Element;
      Stepper: (p: StepperProps) => JSX.Element;
      GoLive: (p: GoLiveProps) => JSX.Element;
      Thread: (p: ThreadProps) => JSX.Element;
      GuardrailScore: (p: GuardrailScoreProps) => JSX.Element;
      ApprovalBanner: (p: ApprovalBannerProps) => JSX.Element;
      Tabs: (p: TabsProps) => JSX.Element;
      TabBar: (p: TabBarProps) => JSX.Element;
      EmptyState: (p: EmptyStateProps) => JSX.Element;
    };
  }
}
