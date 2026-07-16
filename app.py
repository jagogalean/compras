import streamlit as st
import pandas as pd
import psycopg
from psycopg_pool import ConnectionPool
from sqlalchemy import create_engine, text
from contextlib import contextmanager
from datetime import datetime, timedelta
import plotly.express as px
import io

# =====================================================================
# CONFIGURACIÓN DE PÁGINA Y ESTILOS UI
# =====================================================================
st.set_page_config(
    page_title="Strategic Procurement System | Dashboard Ejecutivo",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .reportview-container { background: #f8f9fa; }
    .main-title {
        color: #1E3A8A;
        font-size: 32px;
        font-weight: 700;
        margin-bottom: 5px;
    }
    .metric-card {
        background-color: #ffffff;
        padding: 20px;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        border-left: 5px solid #1E3A8A;
        margin-bottom: 15px;
    }
    .recommendation-box {
        background-color: #ECFDF5;
        border: 1px solid #10B981;
        padding: 20px;
        border-radius: 8px;
        margin-bottom: 20px;
    }
</style>
""", unsafe_allow_html=True)

# =====================================================================
# MOTOR DE BASE DE DATOS EN LA NUBE
# =====================================================================
@st.cache_resource
def get_connection_pool():
    try:
        contrasena = st.secrets["database"]["password"]
    except KeyError:
        st.error("⚠️ Error Crítico: No se encontró la contraseña en el panel de Secrets de Streamlit.")
        raise
    conninfo = (
        f"postgresql://postgres.cotrwpikrtbwqlmbgixq:{contrasena}"
        f"@aws-1-sa-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
    )
    return ConnectionPool(conninfo, min_size=1, max_size=10, open=True)

@contextmanager
def get_db_connection():
    pool_obj = get_connection_pool()
    with pool_obj.connection() as conn:
        yield conn

@st.cache_resource
def get_engine():
    """Motor de SQLAlchemy dedicado a pd.read_sql_query para evitar Segmentation Faults."""
    contrasena = st.secrets["database"]["password"]
    url = (
        f"postgresql+psycopg://postgres.cotrwpikrtbwqlmbgixq:{contrasena}"
        f"@aws-1-sa-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
    )
    return create_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True)

def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    nombre TEXT,
                    email TEXT UNIQUE,
                    rol TEXT,
                    nivel_aprobacion TEXT,
                    secuencia_orden INTEGER DEFAULT 0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    codigo TEXT PRIMARY KEY,
                    descripcion_estandar TEXT,
                    unidad_medida TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS providers (
                    id SERIAL PRIMARY KEY,
                    ruc TEXT UNIQUE,
                    name TEXT,
                    email TEXT,
                    contact_phone TEXT,
                    delivery_score REAL DEFAULT 10,
                    quality_score REAL DEFAULT 10,
                    flexibility_score REAL DEFAULT 10,
                    financial_health_score REAL DEFAULT 10,
                    general_notes TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS areas_emails (
                    id SERIAL PRIMARY KEY,
                    area_name TEXT,
                    email TEXT UNIQUE
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS requisitions (
                    req_code TEXT PRIMARY KEY,
                    situacao_solici TEXT,
                    pedido TEXT,
                    data_aprova DATE,
                    data_solicita DATE,
                    analista_email TEXT,
                    aprobador_actual TEXT,
                    area_name TEXT DEFAULT 'Pendiente de Clasificación',
                    secuencia_aprobacion_actual INTEGER DEFAULT 1
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS requisitions_detalles (
                    id SERIAL PRIMARY KEY,
                    requisicion_id TEXT REFERENCES requisitions(req_code) ON DELETE CASCADE,
                    item_codigo TEXT,
                    narrativa_solicitante TEXT,
                    cantidad_solicitada INTEGER,
                    cantidad_comprador INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id SERIAL PRIMARY KEY,
                    req_code TEXT REFERENCES requisitions(req_code) ON DELETE CASCADE,
                    provider_ruc TEXT,
                    price REAL,
                    payment_terms_days INTEGER,
                    delivery_time_days INTEGER,
                    seleccionado BOOLEAN DEFAULT FALSE
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trazabilidad_cambios (
                    id SERIAL PRIMARY KEY,
                    requisicion_id TEXT,
                    campo_modificado TEXT,
                    valor_anterior TEXT,
                    valor_nuevo TEXT,
                    justificacion TEXT,
                    fecha_cambio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reglas_flujo (
                    id SERIAL PRIMARY KEY,
                    monto_min REAL DEFAULT 0,
                    monto_max REAL,
                    area_name TEXT,
                    empresa TEXT,
                    tipo_compra TEXT,
                    secuencia_requerida INTEGER DEFAULT 1,
                    activo BOOLEAN DEFAULT TRUE
                )
            """)

            # --- Columnas adicionales agregadas de forma NO destructiva ---
            cursor.execute("ALTER TABLE requisitions ADD COLUMN IF NOT EXISTS empresa_ruc TEXT")
            cursor.execute("ALTER TABLE requisitions ADD COLUMN IF NOT EXISTS fecha_comprometida DATE")
            cursor.execute("ALTER TABLE requisitions_detalles ADD COLUMN IF NOT EXISTS cantidad_recibida INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE requisitions_detalles ADD COLUMN IF NOT EXISTS estado_recepcion TEXT DEFAULT 'Pendiente'")

            # --- NUEVO (este fix): campo "nombre" para el catálogo de ítems ---
            # El código sigue existiendo (y se sigue usando como PK / referencia
            # interna), pero ahora también hay un nombre legible por el cual
            # se puede buscar y mostrar el ítem en toda la app.
            cursor.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS nombre TEXT")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS empresas_compradoras (
                    id SERIAL PRIMARY KEY,
                    ruc TEXT UNIQUE,
                    razon_social TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historial_empresa_compradora (
                    id SERIAL PRIMARY KEY,
                    req_code TEXT,
                    empresa_ruc_anterior TEXT,
                    empresa_ruc_nuevo TEXT,
                    usuario TEXT,
                    motivo TEXT,
                    fecha_cambio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documentos_adjuntos (
                    id SERIAL PRIMARY KEY,
                    requisicion_id TEXT,
                    nombre_archivo TEXT,
                    tipo_documento TEXT,
                    contenido BYTEA,
                    subido_por TEXT,
                    fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notificaciones_pendientes (
                    id SERIAL PRIMARY KEY,
                    requisicion_id TEXT,
                    tipo_evento TEXT,
                    mensaje TEXT,
                    fecha_generada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    leida BOOLEAN DEFAULT FALSE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS presupuestos_area (
                    id SERIAL PRIMARY KEY,
                    area_name TEXT,
                    empresa_ruc TEXT,
                    monto_asignado REAL DEFAULT 0,
                    monto_utilizado REAL DEFAULT 0,
                    periodo TEXT,
                    UNIQUE(area_name, empresa_ruc, periodo)
                )
            """)

try:
    init_db()
except Exception as e:
    st.error(f"Error de conexión con Supabase: {e}. Verifica la configuración en Streamlit.")

# =====================================================================
# HELPERS Y UTILIDADES
# =====================================================================
def clean_id(value):
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()

def safe_int(value, default=0):
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default

def call_mock_llm(prompt_type, data):
    if prompt_type == "executive_decision":
        return "**RECOMENDACIÓN DIRECTIVA (10s):** Se aconseja priorizar los flujos con plazos de financiamiento mayores a 30 días para proteger la caja operativa."
    return "Análisis no disponible."

def generar_excel_descarga(columnas, data=None):
    output = io.BytesIO()
    df = pd.DataFrame(data, columns=columnas) if data else pd.DataFrame(columnas)
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Plantilla Modelo')
    return output.getvalue()

ESTADOS_VALIDOS = [
    "Borrador", "Pendiente de aprobación", "En aprobación", "Aprobada", "Rechazada",
    "En cotización", "En negociación", "Orden de Compra emitida", "Parcialmente atendida",
    "Recepción parcial", "Recepción completa", "Cerrada", "Cancelada", "Anulada"
]

def registrar_notificacion(cursor, req_code, tipo_evento, mensaje):
    cursor.execute("""
        INSERT INTO notificaciones_pendientes (requisicion_id, tipo_evento, mensaje)
        VALUES (%s, %s, %s)
    """, (req_code, tipo_evento, mensaje))

def upsert_detalle_linea(cursor, req_code_val, item_cod, narrativa, cantidad):
    cursor.execute("""
        SELECT id, cantidad_solicitada, cantidad_comprador
        FROM requisitions_detalles
        WHERE requisicion_id = %s AND item_codigo = %s
    """, (req_code_val, item_cod))
    existing = cursor.fetchone()

    if existing:
        existing_id, existing_cant_sol, existing_cant_comp = existing
        if existing_cant_comp == existing_cant_sol:
            cursor.execute("""
                UPDATE requisitions_detalles
                SET cantidad_solicitada = %s, narrativa_solicitante = %s, cantidad_comprador = %s
                WHERE id = %s
            """, (cantidad, narrativa, cantidad, existing_id))
        else:
            cursor.execute("""
                UPDATE requisitions_detalles
                SET cantidad_solicitada = %s, narrativa_solicitante = %s
                WHERE id = %s
            """, (cantidad, narrativa, existing_id))
    else:
        cursor.execute("""
            INSERT INTO requisitions_detalles (requisicion_id, item_codigo, narrativa_solicitante, cantidad_solicitada, cantidad_comprador)
            VALUES (%s, %s, %s, %s, %s)
        """, (req_code_val, item_cod, narrativa, cantidad, cantidad))

def obtener_saldo_disponible(area_name):
    if not area_name:
        return 0.0
    df_saldo = pd.read_sql_query(
        "SELECT COALESCE(SUM(monto_asignado - monto_utilizado), 0) AS disponible FROM presupuestos_area WHERE area_name = %(a)s",
        get_engine(), params={"a": area_name}
    )
    return float(df_saldo.iloc[0]['disponible']) if not df_saldo.empty else 0.0

# --- NUEVO (este fix): numeración automática de código de ítem ---
# Formato IT-0001, IT-0002, ... Se calcula en base al máximo código IT-XXXX
# ya cargado, ignorando (sin romper) cualquier código legado que no siga
# ese patrón (p. ej. códigos cargados manualmente antes de este cambio).
def generar_siguiente_codigo_item(cursor):
    cursor.execute("SELECT codigo FROM items WHERE codigo LIKE 'IT-%'")
    codigos = cursor.fetchall()
    max_num = 0
    for (cod,) in codigos:
        try:
            num = int(str(cod).replace('IT-', '').strip())
            if num > max_num:
                max_num = num
        except ValueError:
            continue
    return f"IT-{max_num + 1:04d}"

# --- NUEVO (este fix): catálogo de ítems para selectboxes con nombre visible ---
def obtener_catalogo_items():
    return pd.read_sql_query(
        "SELECT codigo, COALESCE(nombre, '(sin nombre)') AS nombre, descripcion_estandar, unidad_medida "
        "FROM items ORDER BY nombre",
        get_engine()
    )

def selector_item(label, key=None, help_text=None):
    """Devuelve (codigo_seleccionado, df_catalogo). Muestra 'código — nombre'."""
    df_cat = obtener_catalogo_items()
    if df_cat.empty:
        st.warning("No hay ítems cargados en el catálogo todavía.")
        return None, df_cat
    etiquetas = {row['codigo']: f"{row['codigo']} — {row['nombre']}" for _, row in df_cat.iterrows()}
    seleccion = st.selectbox(
        label, df_cat['codigo'].tolist(),
        format_func=lambda c: etiquetas.get(c, c), key=key, help=help_text
    )
    return seleccion, df_cat

# --- NUEVO (este fix): numeración automática robusta de requisiciones ---
# Se usa una expresión regular para tomar en cuenta solo los req_code que
# son puramente numéricos, evitando que un código legado no-numérico rompa
# el cálculo del siguiente correlativo.
def generar_siguiente_req_code(cursor):
    cursor.execute("""
        SELECT req_code FROM requisitions
        WHERE req_code ~ '^[0-9]+$'
        ORDER BY req_code::bigint DESC LIMIT 1
    """)
    ultimo = cursor.fetchone()
    siguiente_num = int(ultimo[0]) + 1 if ultimo else 100000
    return str(siguiente_num)


# =====================================================================
# SISTEMA DE AUTENTICACIÓN IMPLACABLE
# =====================================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.user_email = ""
    st.session_state.user_role = ""
    st.session_state.user_name = ""

if not st.session_state.authenticated:
    st.markdown("<h1 style='text-align: center; color: #1E3A8A; margin-top: 50px;'>Control de Acceso Requerido</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center;'>Identificación obligatoria para ingresar al perímetro de gestión.</p>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            email_input = st.text_input("Correo Institucional (Identificador de Usuario)")
            password_input = st.text_input("Contraseña de Acceso", type="password")
            submit_btn = st.form_submit_button("Someter a Verificación")

            if submit_btn:
                if not email_input or not password_input:
                    st.error("Denegado: Es obligatorio proveer credenciales completas.")
                else:
                    try:
                        df_user = pd.read_sql_query(
                            "SELECT nombre, email, rol FROM usuarios WHERE email = %(email)s",
                            get_engine(),
                            params={"email": email_input.strip()}
                        )
                        if not df_user.empty:
                            st.session_state.authenticated = True
                            st.session_state.user_email = df_user.iloc[0]['email']
                            st.session_state.user_role = df_user.iloc[0]['rol'].lower()
                            st.session_state.user_name = df_user.iloc[0]['nombre']
                            st.rerun()
                        else:
                            st.error("Denegado: Identidad no encontrada en el registro maestro de Supabase.")
                    except Exception as e:
                        st.error(f"Error durante la validación: {e}")
    st.stop()


# =====================================================================
# SIDEBAR (CONTROLES GLOBALES)
# =====================================================================
st.sidebar.markdown(f"<h2 style='color:#1E3A8A; font-weight:700;'>🏛️ Panel de {st.session_state.user_name}</h2>", unsafe_allow_html=True)
st.sidebar.markdown(f"**Rol Detectado:** {st.session_state.user_role.capitalize()}")
st.sidebar.markdown("---")

st.sidebar.subheader("🗂️ Módulos del Sistema")

# --- Roles y permisos ---
# NUEVO (este fix): se agrega "📋 Reportes de Requisiciones" a TODOS los roles.
# Es una vista de solo lectura: cualquiera puede ver el número de requisición,
# sus ítems (con nombre) y en qué nivel de aprobación está. La MODIFICACIÓN
# real de esos datos sigue reservada a "🛠️ Control de Compras", que solo
# aparece para comprador/administración, como ya estaba.
REPORTES_MENU = "📋 Reportes de Requisiciones"

ROLE_MENU_MAP = {
    "solicitante": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📝 Nueva Solicitud",
        REPORTES_MENU,
        "🔍 Buscador Rápido",
    ],
    "jefe de área": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📝 Nueva Solicitud",
        "⚖️ Cuadro Comparativo Masivo",
        REPORTES_MENU,
        "🔍 Buscador Rápido",
    ],
    "comprador": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📥 Mapeador Masivo",
        "⚖️ Cuadro Comparativo Masivo",
        "🛠️ Control de Compras",
        "📊 Dashboard Ejecutivo",
        REPORTES_MENU,
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    "gerencia": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "⚖️ Cuadro Comparativo Masivo",
        "📊 Dashboard Ejecutivo",
        REPORTES_MENU,
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    "directorio": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "⚖️ Cuadro Comparativo Masivo",
        "📊 Dashboard Ejecutivo",
        REPORTES_MENU,
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    "auditoría": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📊 Dashboard Ejecutivo",
        REPORTES_MENU,
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    "administración": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📥 Mapeador Masivo",
        "⚖️ Cuadro Comparativo Masivo",
        "🛠️ Control de Compras",
        "📊 Dashboard Ejecutivo",
        REPORTES_MENU,
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    "aprobador": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "⚖️ Cuadro Comparativo Masivo",
        "📊 Dashboard Ejecutivo",
        REPORTES_MENU,
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
    ],
}

opciones_permitidas = ROLE_MENU_MAP.get(
    st.session_state.user_role,
    ["🏢 Estructura Organizacional", "🤝 Gestión de Proveedores", REPORTES_MENU]
)

opcion_menu = st.sidebar.radio("Seleccione una sección:", opciones_permitidas)

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Filtros Gerenciales")
try:
    df_estados_existentes = pd.read_sql_query(
        "SELECT DISTINCT situacao_solici FROM requisitions WHERE situacao_solici IS NOT NULL", get_engine()
    )
    estados_disponibles = sorted(set(df_estados_existentes['situacao_solici'].tolist()) | set(ESTADOS_VALIDOS))
except Exception:
    estados_disponibles = ESTADOS_VALIDOS

status_filter = st.sidebar.multiselect(
    "Estado General", estados_disponibles,
    default=[e for e in estados_disponibles if e in ("Com Ordem", "Fechada")] or estados_disponibles[:2]
)

st.sidebar.markdown("---")
if st.sidebar.button("Cerrar Sesión Operativa"):
    st.session_state.clear()
    st.rerun()

# =====================================================================
# RENDERIZADO LÓGICO
# =====================================================================
st.markdown(f"<div class='main-title'>{opcion_menu}</div>", unsafe_allow_html=True)
st.markdown("Plataforma analítica sincronizada en tiempo real con Supabase Postgres.")
st.markdown("---")

# 1. ESTRUCTURA ORGANIZACIONAL
if opcion_menu == "🏢 Estructura Organizacional":
    tab_usuarios, tab_ruta = st.tabs(["👥 Directorio de Usuarios", "⛓️ Ruta Crítica de Autorizaciones"])

    with tab_usuarios:
        st.subheader("Personal con acceso e Indexación en el Flujo")
        try:
            df_u = pd.read_sql_query('SELECT nombre AS "Nombre", email AS "Correo Institucional", rol AS "Rol Asignado" FROM usuarios', get_engine())
            if not df_u.empty:
                st.dataframe(df_u, use_container_width=True)
            else:
                st.info("No se registran usuarios cargados en el sistema.")
        except Exception as e:
            st.info("No se registran usuarios cargados en el sistema.")
            with st.expander("Detalle técnico del error"):
                st.code(str(e))

    with tab_ruta:
        st.subheader("Flujo de Aprobadores Secuenciales en Cascada")
        try:
            df_aprob = pd.read_sql_query("""
                SELECT nombre AS "Nombre Aprobador", email AS "Correo",
                       nivel_aprobacion AS "Escalón Jerárquico", secuencia_orden AS "Orden de Firma"
                FROM usuarios WHERE rol = 'aprobador' ORDER BY secuencia_orden ASC
            """, get_engine())
            if not df_aprob.empty:
                st.dataframe(df_aprob, use_container_width=True)
            else:
                st.info("Sin jerarquías configuradas en el maestro.")
        except Exception as e:
            st.info("Sin jerarquías configuradas en el maestro.")
            with st.expander("Detalle técnico del error"):
                st.code(str(e))

# 2. GESTIÓN DE PROVEEDORES
elif opcion_menu == "🤝 Gestión de Proveedores":
    st.subheader("Directorio Maestro e Indicadores de Operabilidad")
    try:
        df_prov = pd.read_sql_query("""
            SELECT ruc AS "RUC", name AS "Razón Social", email AS "Email Comercial",
                   delivery_score AS "Score Entrega", quality_score AS "Score Calidad",
                   flexibility_score AS "Score Flexibilidad",
                   financial_health_score AS "Score Salud Financiera",
                   general_notes AS "Notas Generales"
            FROM providers
        """, get_engine())
        if not df_prov.empty:
            st.dataframe(df_prov, use_container_width=True)
        else:
            st.info("No hay proveedores registrados en la base de datos.")
    except Exception as e:
        st.info("No hay proveedores registrados en la base de datos.")
        with st.expander("Detalle técnico del error"):
            st.code(str(e))

# 3. MAPEADOR MASIVO
elif opcion_menu == "📥 Mapeador Masivo":
    st.subheader("Carga, Validación e Inicialización Masiva de Registros mediante Excel")
    st.caption("Cada pestaña permite cargar un registro individual directamente en pantalla, "
               "o subir un Excel con muchos registros a la vez.")

    t_usuarios, t_items, t_prov, t_areas, t_empresas, t_presu, t_reqs = st.tabs([
        "👥 Carga de Usuarios", "📦 Catálogo de Ítems", "🏢 Directorio Proveedores",
        "🗺️ Áreas y Emails", "🏦 Empresas Compradoras", "💰 Presupuestos",
        "📑 Planilla Maestro Requisiciones"
    ])

    with t_usuarios:
        st.markdown("#### ➕ Cargar un usuario")
        with st.form("form_individual_usuario"):
            fu_nombre = st.text_input("Nombre")
            fu_email = st.text_input("Email")
            fu_rol = st.selectbox("Rol", ["solicitante", "jefe de área", "comprador", "gerencia", "directorio", "auditoría", "administración", "aprobador"])
            fu_nivel = st.text_input("Nivel de aprobación (opcional, solo aplica a rol 'aprobador')")
            fu_secuencia = st.number_input("Secuencia de orden (opcional, solo aplica a rol 'aprobador')", min_value=0, value=0)
            if st.form_submit_button("Guardar Usuario"):
                if not fu_nombre.strip() or not fu_email.strip():
                    st.error("Nombre y email son obligatorios.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                INSERT INTO usuarios (nombre, email, rol, nivel_aprobacion, secuencia_orden)
                                VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO UPDATE SET
                                nombre=EXCLUDED.nombre, rol=EXCLUDED.rol, nivel_aprobacion=EXCLUDED.nivel_aprobacion, secuencia_orden=EXCLUDED.secuencia_orden
                            """, (fu_nombre.strip(), fu_email.strip(), fu_rol, fu_nivel.strip(), safe_int(fu_secuencia)))
                    st.success(f"Usuario {fu_email} guardado.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📊 Carga masiva por Excel")
        cols_u = ['nombre', 'email', 'rol', 'nivel_aprobacion', 'secuencia_orden']
        ejemplo_u = [['Celeste Benítez', 'celeste.benitez@ejemplo.com', 'solicitante', '', 0]]
        st.download_button("📥 Descargar Plantilla Ejemplo (Usuarios)", generar_excel_descarga(cols_u, ejemplo_u), "ejemplo_usuarios.xlsx", "application/vnd.ms-excel")
        up_u = st.file_uploader("Subir planilla de usuarios", type=["xlsx", "csv"], key="u_up")
        if up_u:
            df = pd.read_excel(up_u) if up_u.name.endswith('xlsx') else pd.read_csv(up_u)
            if st.button("🚀 Procesar Carga Masiva de Usuarios"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df.iterrows():
                            cursor.execute("""
                                INSERT INTO usuarios (nombre, email, rol, nivel_aprobacion, secuencia_orden)
                                VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO UPDATE SET
                                nombre=EXCLUDED.nombre, rol=EXCLUDED.rol, nivel_aprobacion=EXCLUDED.nivel_aprobacion, secuencia_orden=EXCLUDED.secuencia_orden
                            """, (r['nombre'], r['email'], r['rol'], r['nivel_aprobacion'], safe_int(r['secuencia_orden'])))
                st.success("Usuarios sincronizados masivamente.")

    # =========================================================
    # NUEVO (este fix): Catálogo de Ítems con código autogenerado,
    # nombre buscable, y buscador antes de crear uno nuevo (para
    # evitar duplicados).
    # =========================================================
    with t_items:
        st.markdown("#### 🔍 Buscar ítems ya cargados")
        buscar_item_txt = st.text_input(
            "Buscar por nombre, código o descripción",
            key="buscar_item_catalogo",
            placeholder="Ej: guantes, IT-0001, nitrilo..."
        )
        query_items_base = "SELECT codigo, COALESCE(nombre,'(sin nombre)') AS nombre, descripcion_estandar, unidad_medida FROM items"
        if buscar_item_txt:
            df_items_existentes = pd.read_sql_query(
                query_items_base + " WHERE nombre ILIKE %(t)s OR codigo ILIKE %(t)s OR descripcion_estandar ILIKE %(t)s ORDER BY nombre",
                get_engine(), params={"t": f"%{buscar_item_txt}%"}
            )
        else:
            df_items_existentes = pd.read_sql_query(query_items_base + " ORDER BY nombre", get_engine())
        st.dataframe(df_items_existentes, use_container_width=True)

        st.markdown("---")
        st.markdown("#### ➕ Cargar un ítem nuevo")
        st.caption("El código se asigna automáticamente (formato IT-0001, IT-0002, ...). "
                   "Solo debe indicar el nombre (por el cual se podrá buscar) y opcionalmente una descripción y unidad de medida.")
        with st.form("form_individual_item"):
            fi_nombre = st.text_input("Nombre del Ítem (obligatorio)")
            fi_desc = st.text_input("Descripción estándar / especificación")
            fi_unidad = st.text_input("Unidad de medida")
            if st.form_submit_button("Guardar Ítem"):
                if not fi_nombre.strip():
                    st.error("El nombre del ítem es obligatorio.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            nuevo_codigo_item = generar_siguiente_codigo_item(cursor)
                            cursor.execute("""
                                INSERT INTO items (codigo, nombre, descripcion_estandar, unidad_medida)
                                VALUES (%s, %s, %s, %s)
                            """, (nuevo_codigo_item, fi_nombre.strip(), fi_desc.strip(), fi_unidad.strip()))
                    st.success(f"Ítem **{fi_nombre}** guardado con código **{nuevo_codigo_item}**.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📊 Carga masiva por Excel")
        st.caption("Nota: en la carga masiva todos los ítems se crean como registros nuevos "
                   "(el código se asigna automáticamente), no se actualizan ítems existentes por esta vía.")
        cols_i = ['nombre', 'descripcion_estandar', 'unidad_medida']
        ejemplo_i = [['Guantes de nitrilo talla M', 'Guantes de nitrilo talla M, caja x100', 'Caja x100']]
        st.download_button("📥 Descargar Plantilla Ejemplo (Ítems)", generar_excel_descarga(cols_i, ejemplo_i), "ejemplo_items.xlsx", "application/vnd.ms-excel")
        up_i = st.file_uploader("Subir planilla del catálogo", type=["xlsx", "csv"], key="i_up")
        if up_i:
            df = pd.read_excel(up_i) if up_i.name.endswith('xlsx') else pd.read_csv(up_i)
            if st.button("🚀 Sincronizar Catálogo de Ítems"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df.iterrows():
                            nombre_val = str(r['nombre']).strip()
                            if not nombre_val or nombre_val.lower() == 'nan':
                                continue
                            nuevo_codigo_item = generar_siguiente_codigo_item(cursor)
                            cursor.execute("""
                                INSERT INTO items (codigo, nombre, descripcion_estandar, unidad_medida)
                                VALUES (%s, %s, %s, %s)
                            """, (nuevo_codigo_item, nombre_val,
                                  str(r.get('descripcion_estandar', '')).strip(),
                                  str(r.get('unidad_medida', '')).strip()))
                st.success("Catálogo maestro actualizado. Se asignó código automático a cada ítem nuevo.")
                st.rerun()

    with t_prov:
        st.markdown("#### ➕ Cargar un proveedor")
        with st.form("form_individual_proveedor"):
            fp_ruc = st.text_input("RUC")
            fp_name = st.text_input("Razón Social")
            fp_email = st.text_input("Email Comercial")
            fp_phone = st.text_input("Teléfono de Contacto")
            if st.form_submit_button("Guardar Proveedor"):
                if not fp_ruc.strip() or not fp_name.strip():
                    st.error("RUC y Razón Social son obligatorios.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                INSERT INTO providers (ruc, name, email, contact_phone)
                                VALUES (%s, %s, %s, %s) ON CONFLICT (ruc) DO UPDATE SET
                                name=EXCLUDED.name, email=EXCLUDED.email, contact_phone=EXCLUDED.contact_phone
                            """, (clean_id(fp_ruc), fp_name.strip(), fp_email.strip(), clean_id(fp_phone)))
                    st.success(f"Proveedor {fp_name} guardado.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📊 Carga masiva por Excel")
        cols_p = ['ruc', 'name', 'email', 'contact_phone']
        ejemplo_p = [['80012345-6', 'Distribuidora Ejemplo SA', 'ventas@distribuidoraejemplo.com', '0981123456']]
        st.download_button("📥 Descargar Plantilla Ejemplo (Proveedores)", generar_excel_descarga(cols_p, ejemplo_p), "ejemplo_proveedores.xlsx", "application/vnd.ms-excel")
        up_p = st.file_uploader("Subir planilla de proveedores", type=["xlsx", "csv"], key="p_up")
        if up_p:
            df = pd.read_excel(up_p) if up_p.name.endswith('xlsx') else pd.read_csv(up_p)
            if st.button("🚀 Inyectar Directorio de Proveedores"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df.iterrows():
                            cursor.execute("""
                                INSERT INTO providers (ruc, name, email, contact_phone)
                                VALUES (%s, %s, %s, %s) ON CONFLICT (ruc) DO UPDATE SET
                                name=EXCLUDED.name, email=EXCLUDED.email, contact_phone=EXCLUDED.contact_phone
                            """, (clean_id(r['ruc']), r['name'], r['email'], clean_id(r['contact_phone'])))
                st.success("Proveedores dados de alta de manera masiva.")

    with t_areas:
        st.markdown("#### ➕ Cargar un área")
        with st.form("form_individual_area"):
            fa_area = st.text_input("Nombre del Área")
            fa_email = st.text_input("Email asociado")
            if st.form_submit_button("Guardar Área"):
                if not fa_area.strip() or not fa_email.strip():
                    st.error("Ambos campos son obligatorios.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                INSERT INTO areas_emails (area_name, email)
                                VALUES (%s, %s) ON CONFLICT (email) DO UPDATE SET
                                area_name=EXCLUDED.area_name
                            """, (fa_area.strip(), fa_email.strip()))
                    st.success(f"Área {fa_area} guardada.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📊 Carga masiva por Excel")
        cols_a = ['area_name', 'email']
        ejemplo_a = [['Mantenimiento', 'mantenimiento@ejemplo.com']]
        st.download_button("📥 Descargar Plantilla Ejemplo (Áreas y Emails)", generar_excel_descarga(cols_a, ejemplo_a), "ejemplo_areas_emails.xlsx", "application/vnd.ms-excel")
        up_a = st.file_uploader("Subir planilla de áreas y emails", type=["xlsx", "csv"], key="a_up")
        if up_a:
            df = pd.read_excel(up_a) if up_a.name.endswith('xlsx') else pd.read_csv(up_a)
            if st.button("🚀 Sincronizar Mapeo de Áreas y Emails"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df.iterrows():
                            cursor.execute("""
                                INSERT INTO areas_emails (area_name, email)
                                VALUES (%s, %s) ON CONFLICT (email) DO UPDATE SET
                                area_name=EXCLUDED.area_name
                            """, (str(r['area_name']).strip(), str(r['email']).strip()))
                st.success("Mapeo de áreas y emails actualizado sin duplicados.")

    with t_empresas:
        st.markdown("#### ➕ Cargar una empresa compradora")
        with st.form("form_individual_empresa"):
            fe_ruc = st.text_input("RUC")
            fe_razon = st.text_input("Razón Social")
            if st.form_submit_button("Guardar Empresa Compradora"):
                if not fe_ruc.strip() or not fe_razon.strip():
                    st.error("Ambos campos son obligatorios.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                INSERT INTO empresas_compradoras (ruc, razon_social)
                                VALUES (%s, %s) ON CONFLICT (ruc) DO UPDATE SET
                                razon_social=EXCLUDED.razon_social
                            """, (clean_id(fe_ruc), fe_razon.strip()))
                    st.success(f"Empresa {fe_razon} guardada.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📊 Carga masiva por Excel")
        cols_emp = ['ruc', 'razon_social']
        ejemplo_emp = [['80098765-4', 'Empresa Compradora Uno SA']]
        st.download_button("📥 Descargar Plantilla Ejemplo (Empresas Compradoras)", generar_excel_descarga(cols_emp, ejemplo_emp), "ejemplo_empresas_compradoras.xlsx", "application/vnd.ms-excel")
        up_emp = st.file_uploader("Subir planilla de empresas compradoras", type=["xlsx", "csv"], key="emp_up")
        if up_emp:
            df = pd.read_excel(up_emp) if up_emp.name.endswith('xlsx') else pd.read_csv(up_emp)
            if st.button("🚀 Sincronizar Empresas Compradoras"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df.iterrows():
                            cursor.execute("""
                                INSERT INTO empresas_compradoras (ruc, razon_social)
                                VALUES (%s, %s) ON CONFLICT (ruc) DO UPDATE SET
                                razon_social=EXCLUDED.razon_social
                            """, (clean_id(r['ruc']), str(r['razon_social']).strip()))
                st.success("Empresas compradoras sincronizadas.")

    with t_presu:
        st.markdown("#### ➕ Cargar un presupuesto")
        with st.form("form_individual_presupuesto"):
            fpr_area = st.text_input("Área")
            fpr_empresa = st.text_input("RUC Empresa Compradora")
            fpr_asignado = st.number_input("Monto Asignado", min_value=0.0, value=0.0)
            fpr_utilizado = st.number_input("Monto Utilizado", min_value=0.0, value=0.0)
            fpr_periodo = st.text_input("Período (ej. 2026-Q3)")
            if st.form_submit_button("Guardar Presupuesto"):
                if not fpr_area.strip() or not fpr_periodo.strip():
                    st.error("Área y Período son obligatorios.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                INSERT INTO presupuestos_area (area_name, empresa_ruc, monto_asignado, monto_utilizado, periodo)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (area_name, empresa_ruc, periodo) DO UPDATE SET
                                monto_asignado=EXCLUDED.monto_asignado, monto_utilizado=EXCLUDED.monto_utilizado
                            """, (fpr_area.strip(), clean_id(fpr_empresa), float(fpr_asignado), float(fpr_utilizado), fpr_periodo.strip()))
                    st.success(f"Presupuesto de {fpr_area} guardado.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📊 Carga masiva por Excel")
        cols_pres = ['area_name', 'empresa_ruc', 'monto_asignado', 'monto_utilizado', 'periodo']
        ejemplo_pres = [['Mantenimiento', '80098765-4', 50000000, 12500000, '2026-Q3']]
        st.download_button("📥 Descargar Plantilla Ejemplo (Presupuestos)", generar_excel_descarga(cols_pres, ejemplo_pres), "ejemplo_presupuestos.xlsx", "application/vnd.ms-excel")
        up_pres = st.file_uploader("Subir planilla de presupuestos", type=["xlsx", "csv"], key="pres_up")
        if up_pres:
            df = pd.read_excel(up_pres) if up_pres.name.endswith('xlsx') else pd.read_csv(up_pres)
            if st.button("🚀 Sincronizar Presupuestos"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df.iterrows():
                            cursor.execute("""
                                INSERT INTO presupuestos_area (area_name, empresa_ruc, monto_asignado, monto_utilizado, periodo)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (area_name, empresa_ruc, periodo) DO UPDATE SET
                                monto_asignado=EXCLUDED.monto_asignado, monto_utilizado=EXCLUDED.monto_utilizado
                            """, (str(r['area_name']).strip(), clean_id(r['empresa_ruc']),
                                  float(r['monto_asignado']), float(r['monto_utilizado']), str(r['periodo']).strip()))
                st.success("Presupuestos por área sincronizados.")

    with t_reqs:
        st.markdown("#### ➕ Cargar una requisición (un ítem)")
        st.caption("Para agregar más ítems a la misma requisición, use el mismo 'Código de Solicitación' varias veces, "
                   "o use 'Control de Compras → Ítems → Agregar ítem adicional' una vez que ya exista.")
        with st.form("form_individual_requisicion"):
            fr_req_code = st.text_input("Código de Solicitación")
            fr_situacao = st.selectbox("Situação Solici", ESTADOS_VALIDOS)
            fr_pedido = st.text_input("Pedido (número de OC, '0' si aún no tiene)", value="0")
            fr_data_aprova = st.date_input("Data Aprova", value=None)
            fr_data_solicita = st.date_input("Data Solicita", value=datetime.now().date())
            fr_email_comp = st.text_input("E-mail Comprador")
            fr_email_aprov = st.text_input("E-mail Aprobador")
            fr_email_solicit = st.text_input("E-mail Solicitante")
            fr_item_codigo = st.text_input("Código de Ítem")
            fr_narrativa = st.text_area("Narrativa del Ítem")
            fr_cantidad = st.number_input("Cantidad Solicitada", min_value=1, value=1)

            if st.form_submit_button("Guardar Requisición"):
                if not fr_req_code.strip() or not fr_item_codigo.strip():
                    st.error("Código de Solicitación y Código de Ítem son obligatorios.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            req_code_val = clean_id(fr_req_code)

                            cursor.execute("SELECT area_name FROM areas_emails WHERE email = %s", (fr_email_solicit.strip(),))
                            area_row = cursor.fetchone()
                            assigned_area = area_row[0] if area_row else "Pendiente de Clasificación"

                            cursor.execute("""
                                INSERT INTO requisitions (req_code, situacao_solici, pedido, data_aprova, data_solicita, analista_email, aprobador_actual, area_name)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (req_code) DO UPDATE SET
                                situacao_solici=EXCLUDED.situacao_solici, pedido=EXCLUDED.pedido, data_aprova=EXCLUDED.data_aprova, aprobador_actual=EXCLUDED.aprobador_actual, area_name=EXCLUDED.area_name
                            """, (req_code_val, fr_situacao, fr_pedido.strip(), fr_data_aprova, fr_data_solicita,
                                  fr_email_comp.strip(), fr_email_aprov.strip(), assigned_area))

                            upsert_detalle_linea(cursor, req_code_val, clean_id(fr_item_codigo), fr_narrativa, safe_int(fr_cantidad))
                    st.success(f"Requisición {req_code_val} guardada.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📊 Carga masiva por Excel")
        cols_r = ['Solicitação', 'Situação Solici', 'Pedido', 'Data Aprova', 'Data Solicita', 'E-mail Comp', 'E-mail Aprov', 'E-mail Solicit', 'Código Item', 'Narrativa Item', 'Cantidad Solicitada']
        ejemplo_r = [['14660', 'Pendiente de aprobación', '0', '', '2026-07-01', 'comprador@ejemplo.com',
                      'aprobador@ejemplo.com', 'celeste.benitez@ejemplo.com', 'IT-0001', 'Guantes de nitrilo talla M', 20]]
        st.download_button("📥 Descargar Estructura Maestro de Compras", generar_excel_descarga(cols_r, ejemplo_r), "plantilla_maestro.xlsx", "application/vnd.ms-excel")
        up_r = st.file_uploader("Arrastra el archivo maestro consolidado aquí", type=["xlsx", "csv"], key="r_up")
        if up_r:
            df = pd.read_excel(up_r) if up_r.name.endswith('xlsx') else pd.read_csv(up_r)
            if st.button("🚀 Ejecutar Motor de Integridad Correlativa"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, row in df.iterrows():
                            req_code_val = clean_id(row['Solicitação'])
                            d_aprova = pd.to_datetime(row['Data Aprova']).date() if pd.notnull(row['Data Aprova']) else None
                            d_solicita = pd.to_datetime(row['Data Solicita']).date() if pd.notnull(row['Data Solicita']) else None

                            cursor.execute("SELECT area_name FROM areas_emails WHERE email = %s", (str(row['E-mail Solicit']).strip(),))
                            area_row = cursor.fetchone()
                            assigned_area = area_row[0] if area_row else "Pendiente de Clasificación"

                            cursor.execute("""
                                INSERT INTO requisitions (req_code, situacao_solici, pedido, data_aprova, data_solicita, analista_email, aprobador_actual, area_name)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (req_code) DO UPDATE SET
                                situacao_solici=EXCLUDED.situacao_solici, pedido=EXCLUDED.pedido, data_aprova=EXCLUDED.data_aprova, aprobador_actual=EXCLUDED.aprobador_actual, area_name=EXCLUDED.area_name
                            """, (req_code_val, str(row['Situação Solici']), str(row['Pedido']), d_aprova, d_solicita, str(row['E-mail Comp']), str(row['E-mail Aprov']), assigned_area))

                            cantidad = safe_int(row['Cantidad Solicitada'])
                            item_cod = clean_id(row['Código Item'])
                            narrativa = str(row['Narrativa Item'])

                            upsert_detalle_linea(cursor, req_code_val, item_cod, narrativa, cantidad)
                st.success("Estructura transaccional mapeada con total integridad en Supabase (cantidad_comprador auditada se preserva).")

# 4. CUADRO COMPARATIVO MASIVO Y FLUJO DE APROBACIÓN JERÁRQUICA
elif opcion_menu == "⚖️ Cuadro Comparativo Masivo":
    if status_filter:
        df_reqs_filtradas = pd.read_sql_query(
            "SELECT req_code FROM requisitions WHERE situacao_solici = ANY(%(estados)s) ORDER BY req_code",
            get_engine(), params={"estados": status_filter}
        )
    else:
        df_reqs_filtradas = pd.read_sql_query(
            "SELECT req_code FROM requisitions ORDER BY req_code", get_engine()
        )
    opciones_req = df_reqs_filtradas['req_code'].tolist() if not df_reqs_filtradas.empty else []

    c_req_code = st.selectbox(
        "Código de Requisición a Gestionar (filtrado por 'Estado General' de la barra lateral)",
        opciones_req if opciones_req else ["14660"]
    )

    col_acc1, col_acc2 = st.columns(2)
    with col_acc1:
        st.markdown("### Paso 1: Obtener Plantilla Estructurada")
        df_detalles_plantilla = pd.read_sql_query("""
            SELECT d.item_codigo, COALESCE(i.nombre,'(sin nombre)') AS item_nombre,
                   d.narrativa_solicitante, d.cantidad_solicitada
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
            WHERE d.requisicion_id = %(req)s
        """, get_engine(), params={"req": c_req_code})

        if not df_detalles_plantilla.empty:
            st.dataframe(df_detalles_plantilla, use_container_width=True)
            cols_presupuesto = ['proveedor_ruc', 'precio_total_usd', 'plazo_pago_dias', 'tiempo_entrega_dias']
            df_plantilla_out = pd.DataFrame(columns=cols_presupuesto)
            df_plantilla_out['item_codigo_requerido'] = df_detalles_plantilla['item_codigo']

            output_p = io.BytesIO()
            with pd.ExcelWriter(output_p, engine='xlsxwriter') as writer:
                df_plantilla_out.to_excel(writer, index=False, sheet_name='Cotizaciones')
            st.download_button("📥 Descargar Excel de Cotización Especializada", output_p.getvalue(), f"Cotizacion_Req_{c_req_code}.xlsx", "application/vnd.ms-excel")
        else:
            st.warning("No se encontraron ítems para este código de requisición.")

    with col_acc2:
        st.markdown("### Paso 2: Carga Masiva de Presupuestos")
        up_quotes = st.file_uploader("Cargar planilla de ofertas completada", type=["xlsx"], key="quotes_masivo")
        if up_quotes:
            df_q = pd.read_excel(up_quotes)
            if st.button("🚀 Consolidar Cuadro Comparativo y Disparar Flujo Jerárquico"):
                cursor_area_check = pd.read_sql_query(
                    "SELECT area_name FROM requisitions WHERE req_code = %(req)s", get_engine(), params={"req": c_req_code}
                )
                area_de_la_req = cursor_area_check.iloc[0]['area_name'] if not cursor_area_check.empty else None
                monto_estimado = float(df_q['precio_total_usd'].min()) if 'precio_total_usd' in df_q.columns and not df_q.empty else 0.0
                saldo_area_req = obtener_saldo_disponible(area_de_la_req)

                if monto_estimado > saldo_area_req:
                    st.error(f"⚠️ Bloqueado por presupuesto: el área '{area_de_la_req}' tiene disponible "
                             f"USD {saldo_area_req:,.2f}, pero la cotización más baja es de USD {monto_estimado:,.2f}. "
                             "No se puede disparar el flujo de aprobación hasta ajustar el presupuesto o la cotización.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            for _, r in df_q.iterrows():
                                cursor.execute("""
                                    INSERT INTO budgets (req_code, provider_ruc, price, payment_terms_days, delivery_time_days)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (c_req_code, clean_id(r['proveedor_ruc']), float(r['precio_total_usd']), safe_int(r['plazo_pago_dias']), safe_int(r['tiempo_entrega_dias'])))

                            cursor.execute("""
                                SELECT email FROM usuarios
                                WHERE rol = 'aprobador' AND secuencia_orden = 1
                                ORDER BY secuencia_orden ASC LIMIT 1
                            """)
                            primer_aprobador = cursor.fetchone()

                            if primer_aprobador:
                                cursor.execute("""
                                    UPDATE requisitions SET
                                    situacao_solici = 'Pendiente Aprobación',
                                    secuencia_aprobacion_actual = 1,
                                    aprobador_actual = %s
                                    WHERE req_code = %s
                                """, (primer_aprobador[0], c_req_code))
                            else:
                                cursor.execute("""
                                    UPDATE requisitions SET
                                    situacao_solici = 'Pendiente Aprobación',
                                    secuencia_aprobacion_actual = 1
                                    WHERE req_code = %s
                                """, (c_req_code,))

                            registrar_notificacion(cursor, c_req_code, 'pendiente_aprobacion',
                                                    f"La requisición {c_req_code} quedó pendiente de aprobación tras cargar cotizaciones.")

                    if primer_aprobador:
                        st.success(f"Ofertas indexadas. La requisición avanzó a la ruta crítica de {primer_aprobador[0]}.")
                    else:
                        st.warning("Ofertas indexadas, pero no hay ningún usuario con rol 'aprobador' y secuencia_orden = 1 "
                                   "cargado en la tabla 'usuarios'. La requisición quedó en 'Pendiente Aprobación' pero "
                                   "sin aprobador asignado — cárguelo en Mapeador Masivo → Carga de Usuarios.")

    st.markdown("---")
    st.subheader(f"Matriz de Comparación Operativa para Requisición: {c_req_code}")
    df_quotes = pd.read_sql_query("""
        SELECT b.provider_ruc AS "RUC Proveedor", p.name AS "Razón Social",
               b.price AS "Precio USD", b.payment_terms_days AS "Plazo Pago (Días)",
               b.delivery_time_days AS "Tiempo Entrega (Días)"
        FROM budgets b
        LEFT JOIN providers p ON b.provider_ruc = p.ruc
        WHERE b.req_code = %(req)s
    """, get_engine(), params={"req": c_req_code})

    if not df_quotes.empty:
        st.dataframe(df_quotes, use_container_width=True)
        st.markdown('<div class="recommendation-box">', unsafe_allow_html=True)
        st.subheader("💡 Análisis de Negociación IA")
        st.markdown(f"*{call_mock_llm('executive_decision', {})}*")
        st.markdown('</div>', unsafe_allow_html=True)

# 5. DASHBOARD EJECUTIVO
elif opcion_menu == "📊 Dashboard Ejecutivo":
    if status_filter:
        df_db = pd.read_sql_query(
            "SELECT * FROM requisitions WHERE situacao_solici = ANY(%(estados)s)",
            get_engine(), params={"estados": status_filter}
        )
    else:
        df_db = pd.read_sql_query("SELECT * FROM requisitions", get_engine())

    if not df_db.empty:
        hoy = datetime.now().date()
        un_mes_atras = hoy - timedelta(days=30)
        df_db['data_aprova_dt'] = pd.to_datetime(df_db['data_aprova']).dt.date

        total = len(df_db)
        con_oc = len(df_db[~df_db['pedido'].astype(str).str.strip().isin(['0', '0.0', 'NaN', '', 'None'])])
        retrasados = len(df_db[(df_db['data_aprova_dt'] < un_mes_atras) & (df_db['pedido'].astype(str).str.strip().isin(['0', '0.0', 'NaN', '', 'None']))])

        k1, k2, k3 = st.columns(3)
        k1.metric("Total Requisiciones en Sistema", total)
        k2.metric("Convertidas en Orden de Compra (OC)", con_oc)
        k3.metric("Alertas Críticas por Retraso (Sin OC)", retrasados)

        st.markdown("---")
        g1, g2 = st.columns(2)
        with g1:
            fig_area = px.histogram(df_db, x='area_name', color='situacao_solici', title="Estatus Estructural de Requisiciones por Área")
            st.plotly_chart(fig_area, use_container_width=True)
        with g2:
            fig_aprov = px.histogram(df_db, x='aprobador_actual', title="Carga Dinámica de Órdenes Retenidas por Autorizante")
            st.plotly_chart(fig_aprov, use_container_width=True)

        st.markdown("---")
        st.subheader("📈 Reportes Ampliados")
        rep1, rep2 = st.columns(2)
        with rep1:
            df_compras_area = df_db.groupby('area_name').size().reset_index(name='cantidad')
            fig_area_rep = px.bar(df_compras_area, x='area_name', y='cantidad', title="Compras por Área")
            st.plotly_chart(fig_area_rep, use_container_width=True)
        with rep2:
            df_compras_comprador = df_db.groupby('analista_email').size().reset_index(name='cantidad')
            fig_comprador_rep = px.bar(df_compras_comprador, x='analista_email', y='cantidad', title="Compras por Comprador Responsable")
            st.plotly_chart(fig_comprador_rep, use_container_width=True)

        if 'empresa_ruc' in df_db.columns and df_db['empresa_ruc'].notna().any():
            df_compras_empresa = df_db.dropna(subset=['empresa_ruc']).groupby('empresa_ruc').size().reset_index(name='cantidad')
            fig_empresa_rep = px.bar(df_compras_empresa, x='empresa_ruc', y='cantidad', title="Compras por Empresa Compradora")
            st.plotly_chart(fig_empresa_rep, use_container_width=True)

        if 'fecha_comprometida' in df_db.columns:
            fechas_compr = pd.to_datetime(df_db['fecha_comprometida'], errors='coerce').dt.date
            vencidas_mask = fechas_compr.notna() & (fechas_compr < hoy)
            st.metric("📌 Órdenes con Fecha Comprometida Vencida", int(vencidas_mask.sum()))

        output_rep = io.BytesIO()
        with pd.ExcelWriter(output_rep, engine='xlsxwriter') as writer:
            df_db.drop(columns=['data_aprova_dt'], errors='ignore').to_excel(writer, index=False, sheet_name='Reporte Requisiciones')
        st.download_button("📥 Exportar Reporte Completo a Excel", output_rep.getvalue(), "reporte_requisiciones.xlsx", "application/vnd.ms-excel")

        st.markdown("---")
        st.subheader("⚠️ Alertas de Posibles Compras Duplicadas")
        df_dupes = pd.read_sql_query("""
            SELECT d.item_codigo, COALESCE(i.nombre,'(sin nombre)') AS item_nombre, d.requisicion_id,
                   COUNT(*) OVER (PARTITION BY d.item_codigo) AS repeticiones
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
        """, get_engine())
        df_dupes_flag = df_dupes[df_dupes['repeticiones'] > 1]
        if not df_dupes_flag.empty:
            st.dataframe(df_dupes_flag, use_container_width=True)
        else:
            st.caption("No se detectaron ítems solicitados en múltiples requisiciones.")

        st.markdown("---")
        st.subheader("🛠️ Auditoría y Modificación de Cantidades por Compras")

        select_req = st.selectbox("Seleccione el código de orden a auditar cantidades:", df_db['req_code'].unique())

        df_detalles = pd.read_sql_query("""
            SELECT d.id, d.item_codigo, COALESCE(i.nombre,'(sin nombre)') AS item_nombre,
                   d.narrativa_solicitante, d.cantidad_solicitada, d.cantidad_comprador
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
            WHERE d.requisicion_id = %(req)s
        """, get_engine(), params={"req": select_req})

        if not df_detalles.empty:
            st.dataframe(df_detalles, use_container_width=True)

            with st.form("form_modificar_cantidades"):
                id_linea = st.number_input("ID de la línea a modificar", min_value=int(df_detalles['id'].min()), max_value=int(df_detalles['id'].max()))
                nueva_cantidad = st.number_input("Nueva Cantidad Autorizada por Compras", min_value=0, value=10)
                justificacion_compra = st.text_area("Justificación del Cambio (Campo Obligatorio)")

                if st.form_submit_button("Guardar Cambios de Auditoría"):
                    if not justificacion_compra.strip():
                        st.error("⚠️ Operación Bloqueada: Debe ingresar un motivo válido para alterar las cantidades originales.")
                    else:
                        row_modificada = df_detalles[df_detalles['id'] == id_linea].iloc[0]
                        valor_anterior_cant = str(row_modificada['cantidad_comprador'])

                        with get_db_connection() as conn:
                            with conn.cursor() as cursor:
                                cursor.execute("UPDATE requisitions_detalles SET cantidad_comprador = %s WHERE id = %s", (nueva_cantidad, id_linea))
                                cursor.execute("""
                                    INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (select_req, 'cantidad_comprador', valor_anterior_cant, str(nueva_cantidad), justificacion_compra))
                        st.success("Cantidad rectificada y registrada en la bitácora de trazabilidad de cambios.")
                        st.rerun()
    else:
        st.info("No se registran datos en la nube.")

# 6. APROBAR / RECHAZAR
elif opcion_menu == "✅ Aprobar / Rechazar":
    st.subheader("Bandeja de Aprobación Pendiente")
    st.caption("Se listan las requisiciones donde usted figura como aprobador actual en la cadena secuencial.")

    df_pendientes = pd.read_sql_query("""
        SELECT req_code, situacao_solici, pedido, area_name, secuencia_aprobacion_actual
        FROM requisitions
        WHERE aprobador_actual = %(email)s
        AND situacao_solici IN ('Pendiente Aprobación', 'En aprobación')
    """, get_engine(), params={"email": st.session_state.user_email})

    if df_pendientes.empty:
        st.info("No tiene requisiciones pendientes de su firma en este momento.")
    else:
        st.dataframe(df_pendientes, use_container_width=True)
        req_a_resolver = st.selectbox("Seleccione la requisición a resolver:", df_pendientes['req_code'].tolist())

        # NUEVO (este fix): se muestra el contenido (ítems con nombre) de la
        # requisición seleccionada antes de aprobar/rechazar, para que el
        # aprobador vea qué está autorizando.
        df_items_aprobar = pd.read_sql_query("""
            SELECT d.item_codigo AS "Código", COALESCE(i.nombre,'(sin nombre)') AS "Nombre del Ítem",
                   d.narrativa_solicitante AS "Descripción/Narrativa",
                   d.cantidad_solicitada AS "Cant. Solicitada", d.cantidad_comprador AS "Cant. Autorizada"
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
            WHERE d.requisicion_id = %(req)s
        """, get_engine(), params={"req": req_a_resolver})
        st.markdown("**Contenido de la requisición:**")
        st.dataframe(df_items_aprobar, use_container_width=True)

        col_ap, col_re = st.columns(2)

        with col_ap:
            st.markdown("**Aprobar**")
            if st.button("✅ Aprobar Requisición"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT secuencia_aprobacion_actual FROM requisitions WHERE req_code = %s", (req_a_resolver,))
                        seq_actual = cursor.fetchone()[0]

                        cursor.execute("""
                            SELECT email FROM usuarios
                            WHERE rol = 'aprobador' AND secuencia_orden > %s
                            ORDER BY secuencia_orden ASC LIMIT 1
                        """, (seq_actual,))
                        siguiente = cursor.fetchone()

                        if siguiente:
                            cursor.execute("""
                                UPDATE requisitions
                                SET secuencia_aprobacion_actual = secuencia_aprobacion_actual + 1,
                                    aprobador_actual = %s,
                                    situacao_solici = 'En aprobación'
                                WHERE req_code = %s
                            """, (siguiente[0], req_a_resolver))
                            nuevo_estado = 'En aprobación'
                        else:
                            cursor.execute("""
                                UPDATE requisitions
                                SET situacao_solici = 'Aprobada'
                                WHERE req_code = %s
                            """, (req_a_resolver,))
                            nuevo_estado = 'Aprobada'

                            cursor.execute("""
                                SELECT price FROM budgets WHERE req_code = %s AND seleccionado = TRUE LIMIT 1
                            """, (req_a_resolver,))
                            precio_sel = cursor.fetchone()
                            if not precio_sel:
                                cursor.execute("SELECT MIN(price) FROM budgets WHERE req_code = %s", (req_a_resolver,))
                                precio_sel = cursor.fetchone()

                            monto_a_debitar = precio_sel[0] if precio_sel and precio_sel[0] is not None else None

                            if monto_a_debitar:
                                cursor.execute("SELECT area_name FROM requisitions WHERE req_code = %s", (req_a_resolver,))
                                area_req_row = cursor.fetchone()
                                area_req_val = area_req_row[0] if area_req_row else None

                                if area_req_val:
                                    cursor.execute("""
                                        SELECT id FROM presupuestos_area
                                        WHERE area_name = %s
                                        ORDER BY (monto_asignado - monto_utilizado) DESC LIMIT 1
                                    """, (area_req_val,))
                                    fila_presu = cursor.fetchone()
                                    if fila_presu:
                                        cursor.execute("""
                                            UPDATE presupuestos_area SET monto_utilizado = monto_utilizado + %s WHERE id = %s
                                        """, (monto_a_debitar, fila_presu[0]))
                                        cursor.execute("""
                                            INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                            VALUES (%s, %s, %s, %s, %s)
                                        """, (req_a_resolver, 'presupuesto_debitado', '-', f"USD {monto_a_debitar:,.2f}",
                                              f"Débito automático de presupuesto del área '{area_req_val}' al aprobar en firme."))

                        cursor.execute("""
                            INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (req_a_resolver, 'situacao_solici', 'Pendiente Aprobación', nuevo_estado,
                              f"Aprobado por {st.session_state.user_email}"))
                        registrar_notificacion(cursor, req_a_resolver, 'aprobacion_avanzada',
                                                f"La requisición {req_a_resolver} avanzó a estado '{nuevo_estado}'.")
                st.success(f"Requisición {req_a_resolver} avanzada. Nuevo estado: {nuevo_estado}")
                st.rerun()

        with col_re:
            st.markdown("**Rechazar**")
            motivo_rechazo = st.text_area("Motivo del rechazo (obligatorio)", key="motivo_rechazo")
            if st.button("❌ Rechazar Requisición"):
                if not motivo_rechazo.strip():
                    st.error("⚠️ Debe ingresar un motivo para rechazar la requisición.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                UPDATE requisitions SET situacao_solici = 'Rechazada' WHERE req_code = %s
                            """, (req_a_resolver,))
                            cursor.execute("""
                                INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                VALUES (%s, %s, %s, %s, %s)
                            """, (req_a_resolver, 'situacao_solici', 'Pendiente Aprobación', 'Rechazada', motivo_rechazo))
                            registrar_notificacion(cursor, req_a_resolver, 'rechazo',
                                                    f"La requisición {req_a_resolver} fue rechazada. Motivo: {motivo_rechazo}")
                    st.success(f"Requisición {req_a_resolver} rechazada y registrada en trazabilidad.")
                    st.rerun()

# 7. NUEVA SOLICITUD
elif opcion_menu == "📝 Nueva Solicitud":
    st.subheader("Crear Nueva Solicitud de Compra")
    st.caption("Una vez enviada, la solicitud queda bloqueada para el Solicitante: cualquier cambio "
               "(cantidades, ítems, cancelación) debe canalizarse a través de Compras (ver 'Control de Compras').")

    df_areas_presu = pd.read_sql_query("SELECT DISTINCT area_name FROM presupuestos_area", get_engine())
    df_areas_emails = pd.read_sql_query("SELECT DISTINCT area_name FROM areas_emails", get_engine())
    areas_disponibles = sorted(set(df_areas_presu['area_name'].dropna().tolist()) | set(df_areas_emails['area_name'].dropna().tolist()))

    if not areas_disponibles:
        st.warning("No hay áreas cargadas todavía (ni en 'Presupuestos' ni en 'Áreas y Emails'). "
                   "Pídale a Administración que cargue al menos una antes de solicitar.")
        area_sol = None
        saldo_area = 0.0
    else:
        area_sol = st.selectbox("Área / Centro de Costo", areas_disponibles, key="area_sol_select")
        saldo_area = obtener_saldo_disponible(area_sol)
        if saldo_area > 0:
            st.success(f"💰 Saldo disponible para '{area_sol}': USD {saldo_area:,.2f}")
        else:
            st.error(f"⚠️ El área '{area_sol}' no tiene saldo disponible (USD {saldo_area:,.2f}). "
                     "No podrá enviar la solicitud hasta que Administración cargue presupuesto para esta área.")

    # NUEVO (este fix): el selector de ítem va FUERA del form (igual que el área)
    # para poder mostrar la descripción del ítem en cuanto se elige, sin
    # esperar al submit del formulario.
    item_sol, df_items_cat_sol = selector_item("Ítem a solicitar", key="item_sol_select")
    if item_sol:
        fila_item = df_items_cat_sol[df_items_cat_sol['codigo'] == item_sol].iloc[0]
        desc_mostrar = fila_item['descripcion_estandar'] or "(sin descripción cargada)"
        unidad_mostrar = fila_item['unidad_medida'] or "-"
        st.info(f"📦 **{fila_item['nombre']}**\n\n{desc_mostrar}  \nUnidad de medida: {unidad_mostrar}")

    with st.form("form_nueva_solicitud"):
        cantidad_sol = st.number_input("Cantidad solicitada", min_value=1, value=1)
        narrativa_sol = st.text_area("Descripción / narrativa de la necesidad (detalle adicional para esta solicitud puntual)")

        if st.form_submit_button("📨 Enviar Solicitud"):
            if not area_sol:
                st.error("⚠️ Bloqueado: no hay ningún área disponible para asociar la solicitud.")
            elif saldo_area <= 0:
                st.error("⚠️ Bloqueado: el área seleccionada no tiene saldo presupuestario disponible.")
            elif not item_sol:
                st.error("No hay ítems cargados en el catálogo todavía.")
            else:
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        nuevo_req_code = generar_siguiente_req_code(cursor)

                        cursor.execute("""
                            INSERT INTO requisitions (req_code, situacao_solici, pedido, data_solicita, analista_email, area_name)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (nuevo_req_code, 'Borrador', '0', datetime.now().date(),
                              st.session_state.user_email, area_sol))

                        cursor.execute("""
                            INSERT INTO requisitions_detalles (requisicion_id, item_codigo, narrativa_solicitante, cantidad_solicitada, cantidad_comprador)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (nuevo_req_code, item_sol, narrativa_sol, cantidad_sol, cantidad_sol))

                        cursor.execute("""
                            INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (nuevo_req_code, 'creacion_solicitud', '-', 'Borrador', f"Creada por {st.session_state.user_email}"))

                        registrar_notificacion(cursor, nuevo_req_code, 'solicitud_creada',
                                                f"Nueva solicitud {nuevo_req_code} creada por {st.session_state.user_email}")
                st.success(f"✅ Solicitud creada con el código **{nuevo_req_code}**. Ya no puede modificarla desde aquí.")
                st.rerun()

    # NUEVO (este fix): "Mis Solicitudes" — cada solicitante ve SOLO las
    # requisiciones que él mismo creó (analista_email = su propio email),
    # no las de otras personas. Es de solo lectura: la modificación sigue
    # reservada a Compras (Control de Compras).
    st.markdown("---")
    st.subheader("📄 Mis Solicitudes Creadas")
    df_mis_reqs = pd.read_sql_query("""
        SELECT req_code AS "N° Requisición", situacao_solici AS "Estado",
               area_name AS "Área", aprobador_actual AS "Aprobador Actual",
               secuencia_aprobacion_actual AS "Nivel de Aprobación", pedido AS "N° Orden de Compra"
        FROM requisitions WHERE analista_email = %(email)s ORDER BY req_code DESC
    """, get_engine(), params={"email": st.session_state.user_email})

    if df_mis_reqs.empty:
        st.caption("Todavía no ha creado ninguna solicitud.")
    else:
        st.dataframe(df_mis_reqs, use_container_width=True)
        req_mia_detalle = st.selectbox(
            "Ver contenido (ítems) de:",
            df_mis_reqs["N° Requisición"].tolist(),
            key="mis_reqs_detalle_select"
        )
        df_detalle_mia = pd.read_sql_query("""
            SELECT d.item_codigo AS "Código", COALESCE(i.nombre,'(sin nombre)') AS "Nombre del Ítem",
                   d.narrativa_solicitante AS "Descripción/Narrativa",
                   d.cantidad_solicitada AS "Cant. Solicitada", d.cantidad_comprador AS "Cant. Autorizada por Compras"
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
            WHERE d.requisicion_id = %(req)s
        """, get_engine(), params={"req": req_mia_detalle})
        st.dataframe(df_detalle_mia, use_container_width=True)

# 8. CONTROL DE COMPRAS
elif opcion_menu == "🛠️ Control de Compras":
    st.subheader("Control Exclusivo de Compras sobre Requisiciones")

    # NUEVO (este fix): en vez de un campo de texto libre "a ciegas" (que
    # obligaba a saber de memoria el código), ahora se ve la lista completa
    # de requisiciones existentes, con buscador, y se elige de un selectbox.
    buscar_ctrl = st.text_input(
        "🔍 Buscar requisición por código, área o estado",
        key="buscar_ctrl_req",
        placeholder="Ej: 14660, Mantenimiento, Pendiente..."
    )
    query_reqs_ctrl = """
        SELECT req_code, situacao_solici, area_name, analista_email, aprobador_actual,
               secuencia_aprobacion_actual, pedido
        FROM requisitions
    """
    if buscar_ctrl:
        df_reqs_ctrl = pd.read_sql_query(
            query_reqs_ctrl + " WHERE req_code ILIKE %(t)s OR area_name ILIKE %(t)s OR situacao_solici ILIKE %(t)s ORDER BY req_code DESC",
            get_engine(), params={"t": f"%{buscar_ctrl}%"}
        )
    else:
        df_reqs_ctrl = pd.read_sql_query(query_reqs_ctrl + " ORDER BY req_code DESC", get_engine())

    if df_reqs_ctrl.empty:
        st.warning("No hay requisiciones cargadas en el sistema todavía (o ninguna coincide con la búsqueda).")
        st.stop()

    st.dataframe(df_reqs_ctrl, use_container_width=True)

    ctrl_req_code = st.selectbox(
        "Código de Requisición a intervenir",
        df_reqs_ctrl['req_code'].tolist(),
        key="ctrl_req"
    )

    tab_items, tab_prov_resp, tab_empresa, tab_estado, tab_consolida, tab_docs, tab_recepcion = st.tabs([
        "📦 Ítems", "🤝 Proveedor / Responsable", "🏦 Empresa Compradora",
        "🔄 Estado", "🔀 Consolidar / Dividir OC", "📎 Documentos", "📥 Recepción"
    ])

    with tab_items:
        # NUEVO (este fix): se hace JOIN con items para mostrar el nombre
        # del ítem, no solo el código.
        df_det_ctrl = pd.read_sql_query("""
            SELECT d.id, d.item_codigo, COALESCE(i.nombre,'(sin nombre)') AS item_nombre,
                   d.narrativa_solicitante, d.cantidad_solicitada, d.cantidad_comprador
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
            WHERE d.requisicion_id = %(req)s
        """, get_engine(), params={"req": ctrl_req_code})
        st.dataframe(df_det_ctrl, use_container_width=True)

        accion_item = st.selectbox("Acción sobre ítems", [
            "Agregar ítem adicional", "Eliminar ítem", "Sustituir por equivalente", "Modificar especificaciones técnicas"
        ])

        if accion_item == "Agregar ítem adicional":
            nuevo_codigo, _ = selector_item("Ítem del catálogo a agregar", key="agregar_item_sel")
            with st.form("form_agregar_item"):
                nueva_cant = st.number_input("Cantidad", min_value=1, value=1)
                motivo_add = st.text_area("Motivo de la adición (obligatorio)")
                if st.form_submit_button("➕ Agregar Ítem"):
                    if not motivo_add.strip():
                        st.error("Debe indicar un motivo.")
                    elif not nuevo_codigo:
                        st.error("Debe seleccionar un ítem del catálogo.")
                    else:
                        with get_db_connection() as conn:
                            with conn.cursor() as cursor:
                                cursor.execute("""
                                    INSERT INTO requisitions_detalles (requisicion_id, item_codigo, narrativa_solicitante, cantidad_solicitada, cantidad_comprador)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (ctrl_req_code, nuevo_codigo, "Agregado por Compras", nueva_cant, nueva_cant))
                                cursor.execute("""
                                    INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (ctrl_req_code, 'item_agregado', '-', nuevo_codigo, motivo_add))
                        st.success("Ítem agregado y registrado en trazabilidad.")
                        st.rerun()

        elif accion_item == "Eliminar ítem":
            if not df_det_ctrl.empty:
                with st.form("form_eliminar_item"):
                    id_a_borrar = st.selectbox(
                        "Línea a eliminar (ID)", df_det_ctrl['id'].tolist(),
                        format_func=lambda i: f"{i} — {df_det_ctrl[df_det_ctrl['id']==i].iloc[0]['item_nombre']}"
                    )
                    motivo_del = st.text_area("Motivo de la eliminación (obligatorio)")
                    if st.form_submit_button("🗑️ Eliminar Ítem"):
                        if not motivo_del.strip():
                            st.error("Debe indicar un motivo.")
                        else:
                            codigo_borrado = df_det_ctrl[df_det_ctrl['id'] == id_a_borrar].iloc[0]['item_codigo']
                            with get_db_connection() as conn:
                                with conn.cursor() as cursor:
                                    cursor.execute("DELETE FROM requisitions_detalles WHERE id = %s", (id_a_borrar,))
                                    cursor.execute("""
                                        INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                        VALUES (%s, %s, %s, %s, %s)
                                    """, (ctrl_req_code, 'item_eliminado', codigo_borrado, '-', motivo_del))
                            st.success("Ítem eliminado y registrado en trazabilidad.")
                            st.rerun()
            else:
                st.info("No hay ítems para eliminar.")

        elif accion_item == "Sustituir por equivalente":
            if not df_det_ctrl.empty:
                id_a_sustituir = st.selectbox(
                    "Línea a sustituir (ID)", df_det_ctrl['id'].tolist(),
                    format_func=lambda i: f"{i} — {df_det_ctrl[df_det_ctrl['id']==i].iloc[0]['item_nombre']}",
                    key="sustituir_id_sel"
                )
                nuevo_codigo_sust, _ = selector_item("Nuevo ítem equivalente", key="sustituir_item_sel")
                with st.form("form_sustituir_item"):
                    motivo_sust = st.text_area("Motivo de la sustitución (obligatorio)")
                    if st.form_submit_button("🔁 Sustituir Ítem"):
                        if not motivo_sust.strip():
                            st.error("Debe indicar un motivo.")
                        elif not nuevo_codigo_sust:
                            st.error("Debe seleccionar el ítem equivalente.")
                        else:
                            codigo_anterior = df_det_ctrl[df_det_ctrl['id'] == id_a_sustituir].iloc[0]['item_codigo']
                            with get_db_connection() as conn:
                                with conn.cursor() as cursor:
                                    cursor.execute("UPDATE requisitions_detalles SET item_codigo = %s WHERE id = %s", (nuevo_codigo_sust, id_a_sustituir))
                                    cursor.execute("""
                                        INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                        VALUES (%s, %s, %s, %s, %s)
                                    """, (ctrl_req_code, 'item_sustituido', codigo_anterior, nuevo_codigo_sust, motivo_sust))
                            st.success("Ítem sustituido y registrado en trazabilidad.")
                            st.rerun()
            else:
                st.info("No hay ítems para sustituir.")

        elif accion_item == "Modificar especificaciones técnicas":
            if not df_det_ctrl.empty:
                with st.form("form_modif_specs"):
                    id_a_modif = st.selectbox(
                        "Línea a modificar (ID)", df_det_ctrl['id'].tolist(),
                        format_func=lambda i: f"{i} — {df_det_ctrl[df_det_ctrl['id']==i].iloc[0]['item_nombre']}"
                    )
                    nueva_narrativa = st.text_area("Nueva especificación técnica / narrativa")
                    motivo_specs = st.text_area("Motivo del cambio (obligatorio)", key="motivo_specs")
                    if st.form_submit_button("✏️ Guardar Especificaciones"):
                        if not motivo_specs.strip():
                            st.error("Debe indicar un motivo.")
                        else:
                            narrativa_anterior = df_det_ctrl[df_det_ctrl['id'] == id_a_modif].iloc[0]['narrativa_solicitante']
                            with get_db_connection() as conn:
                                with conn.cursor() as cursor:
                                    cursor.execute("UPDATE requisitions_detalles SET narrativa_solicitante = %s WHERE id = %s", (nueva_narrativa, id_a_modif))
                                    cursor.execute("""
                                        INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                        VALUES (%s, %s, %s, %s, %s)
                                    """, (ctrl_req_code, 'especificacion_tecnica', str(narrativa_anterior), nueva_narrativa, motivo_specs))
                            st.success("Especificaciones actualizadas y registradas en trazabilidad.")
                            st.rerun()
            else:
                st.info("No hay ítems para modificar.")

    with tab_prov_resp:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Cambiar Proveedor Asignado**")
            df_provs = pd.read_sql_query("SELECT ruc, name FROM providers", get_engine())
            with st.form("form_cambiar_proveedor"):
                nuevo_ruc = st.selectbox("Nuevo proveedor", df_provs['ruc'].tolist() if not df_provs.empty else [])
                motivo_prov = st.text_area("Motivo (obligatorio)", key="motivo_prov")
                if st.form_submit_button("Guardar Proveedor Asignado"):
                    if not motivo_prov.strip():
                        st.error("Debe indicar un motivo.")
                    else:
                        with get_db_connection() as conn:
                            with conn.cursor() as cursor:
                                cursor.execute("UPDATE budgets SET seleccionado = FALSE WHERE req_code = %s", (ctrl_req_code,))
                                cursor.execute("UPDATE budgets SET seleccionado = TRUE WHERE req_code = %s AND provider_ruc = %s", (ctrl_req_code, nuevo_ruc))
                                cursor.execute("""
                                    INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (ctrl_req_code, 'proveedor_asignado', '-', nuevo_ruc, motivo_prov))
                        st.success("Proveedor asignado actualizado.")
                        st.rerun()

        with col_b:
            st.markdown("**Reasignar Responsable / Comprador**")
            df_compradores = pd.read_sql_query("SELECT email, nombre FROM usuarios WHERE rol IN ('comprador','administración','aprobador')", get_engine())
            with st.form("form_reasignar"):
                nuevo_resp = st.selectbox("Nuevo comprador responsable", df_compradores['email'].tolist() if not df_compradores.empty else [])
                motivo_resp = st.text_area("Motivo (obligatorio)", key="motivo_resp")
                if st.form_submit_button("Guardar Reasignación"):
                    if not motivo_resp.strip():
                        st.error("Debe indicar un motivo.")
                    else:
                        with get_db_connection() as conn:
                            with conn.cursor() as cursor:
                                cursor.execute("SELECT analista_email FROM requisitions WHERE req_code = %s", (ctrl_req_code,))
                                anterior = cursor.fetchone()
                                anterior_val = anterior[0] if anterior else "-"
                                cursor.execute("UPDATE requisitions SET analista_email = %s WHERE req_code = %s", (nuevo_resp, ctrl_req_code))
                                cursor.execute("""
                                    INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (ctrl_req_code, 'responsable_comprador', anterior_val, nuevo_resp, motivo_resp))
                        st.success("Responsable reasignado.")
                        st.rerun()

    with tab_empresa:
        df_empresas = pd.read_sql_query("SELECT ruc, razon_social FROM empresas_compradoras", get_engine())
        if df_empresas.empty:
            st.warning("No hay empresas compradoras cargadas. Súbalas en Mapeador Masivo → Empresas Compradoras.")
        else:
            df_emp_actual = pd.read_sql_query("SELECT empresa_ruc, area_name FROM requisitions WHERE req_code = %(req)s", get_engine(), params={"req": ctrl_req_code})
            empresa_actual = df_emp_actual.iloc[0]['empresa_ruc'] if not df_emp_actual.empty and pd.notnull(df_emp_actual.iloc[0]['empresa_ruc']) else "Sin asignar"
            area_actual = df_emp_actual.iloc[0]['area_name'] if not df_emp_actual.empty else None
            st.markdown(f"**Empresa compradora actual:** {empresa_actual}")

            with st.form("form_empresa_compradora"):
                nueva_empresa = st.selectbox("Empresa compradora", df_empresas['ruc'].tolist())
                motivo_emp = st.text_area("Motivo del cambio (obligatorio)")
                if st.form_submit_button("Guardar Empresa Compradora"):
                    if not motivo_emp.strip():
                        st.error("Debe indicar un motivo.")
                    else:
                        with get_db_connection() as conn:
                            with conn.cursor() as cursor:
                                cursor.execute("UPDATE requisitions SET empresa_ruc = %s WHERE req_code = %s", (nueva_empresa, ctrl_req_code))
                                cursor.execute("""
                                    INSERT INTO historial_empresa_compradora (req_code, empresa_ruc_anterior, empresa_ruc_nuevo, usuario, motivo)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (ctrl_req_code, str(empresa_actual), nueva_empresa, st.session_state.user_email, motivo_emp))
                                cursor.execute("""
                                    INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (ctrl_req_code, 'empresa_compradora', str(empresa_actual), nueva_empresa, motivo_emp))
                        st.success("Empresa compradora actualizada.")
                        st.rerun()

            df_hist_emp = pd.read_sql_query(
                "SELECT empresa_ruc_anterior, empresa_ruc_nuevo, usuario, motivo, fecha_cambio FROM historial_empresa_compradora WHERE req_code = %(req)s ORDER BY fecha_cambio DESC",
                get_engine(), params={"req": ctrl_req_code}
            )
            if not df_hist_emp.empty:
                st.markdown("**Historial de cambios de empresa compradora**")
                st.dataframe(df_hist_emp, use_container_width=True)

            if area_actual:
                df_presu = pd.read_sql_query(
                    "SELECT empresa_ruc, monto_asignado, monto_utilizado, periodo FROM presupuestos_area WHERE area_name = %(a)s ORDER BY periodo DESC",
                    get_engine(), params={"a": area_actual}
                )
                if not df_presu.empty:
                    df_presu['disponible'] = df_presu['monto_asignado'] - df_presu['monto_utilizado']
                    st.markdown("**💰 Presupuesto disponible para el área** (informativo — no bloquea la emisión de OC en esta versión)")
                    st.dataframe(df_presu, use_container_width=True)

    with tab_estado:
        df_estado_actual = pd.read_sql_query("SELECT situacao_solici FROM requisitions WHERE req_code = %(req)s", get_engine(), params={"req": ctrl_req_code})
        estado_actual = df_estado_actual.iloc[0]['situacao_solici'] if not df_estado_actual.empty else "Sin datos"
        st.markdown(f"**Estado actual:** {estado_actual}")

        with st.form("form_cambiar_estado"):
            nuevo_estado_sel = st.selectbox("Nuevo estado", ESTADOS_VALIDOS)
            motivo_estado = st.text_area("Motivo del cambio de estado (obligatorio)")
            if st.form_submit_button("Actualizar Estado"):
                if not motivo_estado.strip():
                    st.error("Debe indicar un motivo.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("UPDATE requisitions SET situacao_solici = %s WHERE req_code = %s", (nuevo_estado_sel, ctrl_req_code))
                            cursor.execute("""
                                INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                VALUES (%s, %s, %s, %s, %s)
                            """, (ctrl_req_code, 'situacao_solici', str(estado_actual), nuevo_estado_sel, motivo_estado))
                            registrar_notificacion(cursor, ctrl_req_code, 'cambio_estado',
                                                    f"La requisición {ctrl_req_code} cambió a estado '{nuevo_estado_sel}'.")
                    st.success("Estado actualizado y notificación registrada.")
                    st.rerun()

    with tab_consolida:
        st.markdown("**Consolidar varias solicitudes en una sola Orden de Compra**")
        todas_reqs = pd.read_sql_query("SELECT req_code FROM requisitions ORDER BY req_code", get_engine())
        with st.form("form_consolidar"):
            reqs_a_consolidar = st.multiselect("Seleccione las requisiciones a consolidar", todas_reqs['req_code'].tolist())
            numero_oc_consolidada = st.text_input("Número de Orden de Compra consolidada")
            motivo_consol = st.text_area("Motivo / observación (obligatorio)")
            if st.form_submit_button("🔗 Consolidar en una OC"):
                if not reqs_a_consolidar or not numero_oc_consolidada.strip() or not motivo_consol.strip():
                    st.error("Complete todos los campos.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            for req in reqs_a_consolidar:
                                cursor.execute("UPDATE requisitions SET pedido = %s, situacao_solici = 'Orden de Compra emitida' WHERE req_code = %s", (numero_oc_consolidada, req))
                                cursor.execute("""
                                    INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (req, 'consolidacion_oc', '-', numero_oc_consolidada, motivo_consol))
                                registrar_notificacion(cursor, req, 'oc_emitida', f"Orden de Compra consolidada {numero_oc_consolidada} emitida.")
                    st.success(f"{len(reqs_a_consolidar)} requisiciones consolidadas en la OC {numero_oc_consolidada}.")
                    st.rerun()

        st.markdown("---")
        st.markdown("**Dividir una solicitud en varias Órdenes de Compra**")
        df_det_dividir = pd.read_sql_query("""
            SELECT d.id, d.item_codigo, COALESCE(i.nombre,'(sin nombre)') AS item_nombre
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
            WHERE d.requisicion_id = %(req)s
        """, get_engine(), params={"req": ctrl_req_code})
        with st.form("form_dividir"):
            ids_grupo_2 = st.multiselect(
                "Ítems (ID) que van a la NUEVA OC (el resto queda en la OC original)",
                df_det_dividir['id'].tolist() if not df_det_dividir.empty else [],
                format_func=lambda i: f"{i} — {df_det_dividir[df_det_dividir['id']==i].iloc[0]['item_nombre']}" if not df_det_dividir.empty else str(i)
            )
            nuevo_req_code_split = st.text_input("Código de la nueva requisición/OC derivada")
            motivo_div = st.text_area("Motivo de la división (obligatorio)", key="motivo_div")
            if st.form_submit_button("✂️ Dividir en Nueva OC"):
                if not ids_grupo_2 or not nuevo_req_code_split.strip() or not motivo_div.strip():
                    st.error("Complete todos los campos.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                INSERT INTO requisitions (req_code, situacao_solici, pedido, area_name)
                                SELECT %s, situacao_solici, pedido, area_name FROM requisitions WHERE req_code = %s
                                ON CONFLICT (req_code) DO NOTHING
                            """, (nuevo_req_code_split, ctrl_req_code))
                            cursor.execute("UPDATE requisitions_detalles SET requisicion_id = %s WHERE id = ANY(%s)", (nuevo_req_code_split, ids_grupo_2))
                            cursor.execute("""
                                INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                VALUES (%s, %s, %s, %s, %s)
                            """, (ctrl_req_code, 'division_oc', ctrl_req_code, nuevo_req_code_split, motivo_div))
                    st.success(f"Ítems movidos a la nueva requisición/OC {nuevo_req_code_split}.")
                    st.rerun()

    with tab_docs:
        st.caption("MVP: los documentos se guardan como metadata + contenido (BYTEA) directamente en la base de datos. "
                   "Si se prefiere Supabase Storage más adelante, se puede migrar sin cambiar esta interfaz.")
        tipo_doc = st.selectbox("Tipo de documento", ["Cotización", "Factura Proforma", "Especificación Técnica", "Contrato", "Foto", "Correo", "Otro"])
        archivo_subido = st.file_uploader("Adjuntar archivo", key="doc_upload")
        if archivo_subido and st.button("📎 Guardar Documento"):
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO documentos_adjuntos (requisicion_id, nombre_archivo, tipo_documento, contenido, subido_por)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (ctrl_req_code, archivo_subido.name, tipo_doc, archivo_subido.read(), st.session_state.user_email))
            st.success("Documento adjuntado correctamente.")
            st.rerun()

        df_docs = pd.read_sql_query(
            "SELECT nombre_archivo, tipo_documento, subido_por, fecha_subida FROM documentos_adjuntos WHERE requisicion_id = %(req)s ORDER BY fecha_subida DESC",
            get_engine(), params={"req": ctrl_req_code}
        )
        if not df_docs.empty:
            st.dataframe(df_docs, use_container_width=True)
        else:
            st.info("No hay documentos adjuntos para esta requisición.")

    with tab_recepcion:
        df_fecha = pd.read_sql_query("SELECT fecha_comprometida FROM requisitions WHERE req_code = %(req)s", get_engine(), params={"req": ctrl_req_code})
        fecha_actual = df_fecha.iloc[0]['fecha_comprometida'] if not df_fecha.empty else None
        st.markdown(f"**Fecha comprometida actual:** {fecha_actual if fecha_actual else 'No definida'}")
        with st.form("form_fecha_compr"):
            nueva_fecha = st.date_input("Nueva fecha comprometida de entrega")
            if st.form_submit_button("Guardar Fecha Comprometida"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("UPDATE requisitions SET fecha_comprometida = %s WHERE req_code = %s", (nueva_fecha, ctrl_req_code))
                st.success("Fecha comprometida guardada.")
                st.rerun()

        st.markdown("---")
        st.markdown("**Recepción de Ítems (parcial / completa)**")
        df_det_recep = pd.read_sql_query("""
            SELECT d.id, d.item_codigo, COALESCE(i.nombre,'(sin nombre)') AS item_nombre,
                   d.cantidad_comprador, d.cantidad_recibida, d.estado_recepcion
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
            WHERE d.requisicion_id = %(req)s
        """, get_engine(), params={"req": ctrl_req_code})
        if not df_det_recep.empty:
            st.dataframe(df_det_recep, use_container_width=True)
            with st.form("form_recepcion"):
                id_recep = st.selectbox(
                    "Línea a recepcionar (ID)", df_det_recep['id'].tolist(),
                    format_func=lambda i: f"{i} — {df_det_recep[df_det_recep['id']==i].iloc[0]['item_nombre']}"
                )
                cantidad_recibida_input = st.number_input("Cantidad recibida (acumulada)", min_value=0, value=0)
                if st.form_submit_button("📥 Registrar Recepción"):
                    fila = df_det_recep[df_det_recep['id'] == id_recep].iloc[0]
                    cant_comprada = fila['cantidad_comprador'] or 0
                    if cantidad_recibida_input <= 0:
                        estado_recep_nuevo = 'Pendiente'
                    elif cantidad_recibida_input < cant_comprada:
                        estado_recep_nuevo = 'Recepción parcial'
                    else:
                        estado_recep_nuevo = 'Recepción completa'
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                UPDATE requisitions_detalles SET cantidad_recibida = %s, estado_recepcion = %s WHERE id = %s
                            """, (cantidad_recibida_input, estado_recep_nuevo, id_recep))
                            cursor.execute("""
                                INSERT INTO trazabilidad_cambios (requisicion_id, campo_modificado, valor_anterior, valor_nuevo, justificacion)
                                VALUES (%s, %s, %s, %s, %s)
                            """, (ctrl_req_code, 'recepcion_item', str(fila['cantidad_recibida']), str(cantidad_recibida_input),
                                  f"Actualización de recepción a {estado_recep_nuevo}"))
                    st.success(f"Recepción registrada: {estado_recep_nuevo}")
                    st.rerun()
        else:
            st.info("No hay ítems para recepcionar.")

# =========================================================
# NUEVO (este fix): 9. REPORTES DE REQUISICIONES (solo lectura, para todos)
# =========================================================
elif opcion_menu == REPORTES_MENU:
    st.subheader("Reporte General de Requisiciones")
    st.caption("Vista de solo lectura disponible para todos los roles: aquí puede ver el número de cada "
               "requisición, su contenido (ítems) y en qué nivel de aprobación se encuentra. "
               "La modificación de estos datos está reservada exclusivamente al equipo de Compras, "
               "desde la sección '🛠️ Control de Compras'.")

    buscar_rep = st.text_input(
        "🔍 Buscar por número de requisición, área o estado",
        placeholder="Ej: 100001, Mantenimiento, Aprobada..."
    )
    query_rep = """
        SELECT req_code AS "N° Requisición", situacao_solici AS "Estado", area_name AS "Área",
               analista_email AS "Creado por", aprobador_actual AS "Aprobador Actual",
               secuencia_aprobacion_actual AS "Nivel de Aprobación", pedido AS "N° Orden de Compra"
        FROM requisitions
    """
    if buscar_rep:
        df_rep = pd.read_sql_query(
            query_rep + " WHERE req_code ILIKE %(t)s OR area_name ILIKE %(t)s OR situacao_solici ILIKE %(t)s ORDER BY req_code DESC",
            get_engine(), params={"t": f"%{buscar_rep}%"}
        )
    else:
        df_rep = pd.read_sql_query(query_rep + " ORDER BY req_code DESC", get_engine())

    if df_rep.empty:
        st.info("No hay requisiciones registradas (o ninguna coincide con la búsqueda).")
    else:
        st.dataframe(df_rep, use_container_width=True)
        st.markdown("---")
        req_ver_detalle = st.selectbox("Ver contenido (ítems) de la requisición:", df_rep["N° Requisición"].tolist())
        df_items_rep = pd.read_sql_query("""
            SELECT d.item_codigo AS "Código", COALESCE(i.nombre,'(sin nombre)') AS "Nombre del Ítem",
                   d.narrativa_solicitante AS "Descripción/Narrativa",
                   d.cantidad_solicitada AS "Cant. Solicitada", d.cantidad_comprador AS "Cant. Autorizada",
                   d.cantidad_recibida AS "Cant. Recibida", d.estado_recepcion AS "Estado de Recepción"
            FROM requisitions_detalles d LEFT JOIN items i ON d.item_codigo = i.codigo
            WHERE d.requisicion_id = %(req)s
        """, get_engine(), params={"req": req_ver_detalle})
        st.dataframe(df_items_rep, use_container_width=True)

# 10. BUSCADOR RÁPIDO
elif opcion_menu == "🔍 Buscador Rápido":
    st.subheader("Buscador Rápido")
    termino = st.text_input("Buscar por número de solicitud, proveedor o producto")
    if termino:
        df_busq_reqs = pd.read_sql_query(
            "SELECT req_code, situacao_solici, area_name FROM requisitions WHERE req_code ILIKE %(t)s",
            get_engine(), params={"t": f"%{termino}%"}
        )
        df_busq_prov = pd.read_sql_query(
            "SELECT ruc, name FROM providers WHERE name ILIKE %(t)s OR ruc ILIKE %(t)s",
            get_engine(), params={"t": f"%{termino}%"}
        )
        # NUEVO (este fix): el buscador de productos ahora también busca
        # (y muestra) por el campo "nombre", no solo por descripción/código.
        df_busq_item = pd.read_sql_query(
            "SELECT codigo, COALESCE(nombre,'(sin nombre)') AS nombre, descripcion_estandar "
            "FROM items WHERE nombre ILIKE %(t)s OR descripcion_estandar ILIKE %(t)s OR codigo ILIKE %(t)s",
            get_engine(), params={"t": f"%{termino}%"}
        )
        st.markdown("**Solicitudes**")
        st.dataframe(df_busq_reqs, use_container_width=True) if not df_busq_reqs.empty else st.caption("Sin resultados.")
        st.markdown("**Proveedores**")
        st.dataframe(df_busq_prov, use_container_width=True) if not df_busq_prov.empty else st.caption("Sin resultados.")
        st.markdown("**Productos**")
        st.dataframe(df_busq_item, use_container_width=True) if not df_busq_item.empty else st.caption("Sin resultados.")
    else:
        st.caption("Ingrese un término de búsqueda para comenzar.")

# 11. NOTIFICACIONES
elif opcion_menu == "🔔 Notificaciones":
    st.subheader("Bitácora de Notificaciones Pendientes / Generadas")
    st.caption("MVP: log de eventos en base de datos. No hay envío de email real configurado todavía.")
    df_notifs = pd.read_sql_query(
        "SELECT requisicion_id, tipo_evento, mensaje, fecha_generada, leida FROM notificaciones_pendientes ORDER BY fecha_generada DESC LIMIT 200",
        get_engine()
    )
    if not df_notifs.empty:
        st.dataframe(df_notifs, use_container_width=True)
    else:
        st.info("No hay notificaciones registradas.")
