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
            # --- NUEVO (Requerimiento 7): tabla de reglas de flujo de aprobación ---
            # Versión simple inicial: guarda condiciones que a futuro pueden usarse
            # para determinar la secuencia de aprobación requerida según monto/área/
            # empresa/tipo de compra. No se conecta aún a lógica automática de ruteo;
            # eso queda para una siguiente iteración según lo indicado en el informe.
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

            # --- NUEVO (Fase 4 y 5): columnas adicionales, agregadas de forma NO
            # destructiva con ADD COLUMN IF NOT EXISTS. No se toca ningún dato existente.
            cursor.execute("ALTER TABLE requisitions ADD COLUMN IF NOT EXISTS empresa_ruc TEXT")
            cursor.execute("ALTER TABLE requisitions ADD COLUMN IF NOT EXISTS fecha_comprometida DATE")
            cursor.execute("ALTER TABLE requisitions_detalles ADD COLUMN IF NOT EXISTS cantidad_recibida INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE requisitions_detalles ADD COLUMN IF NOT EXISTS estado_recepcion TEXT DEFAULT 'Pendiente'")

            # --- NUEVO (Requerimiento 4): Empresa Compradora (16 RUC distintos) ---
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

            # --- NUEVO (Requerimiento 9): Gestión documental ---
            # MVP: se guarda el contenido como BYTEA directamente en Postgres para no
            # sumar una dependencia nueva (supabase-py / Storage SDK). Si más adelante
            # se prefiere Supabase Storage, se puede migrar sin tocar la interfaz.
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

            # --- NUEVO (Requerimiento 10): Notificaciones automáticas ---
            # MVP: log de notificaciones pendientes en base de datos (no envío de email real,
            # tal como habilita el informe: "como mínimo un log de notificaciones pendientes").
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

            # --- NUEVO (recomendado): Control de presupuesto disponible ---
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

# --- NUEVO (Requerimiento 5): set completo de estados del proceso ---
# Reemplaza el uso de texto libre en situacao_solici. Se mantiene compatibilidad
# con valores ya cargados en producción (ver sidebar, que fusiona estos con los
# que ya existan en la base) para no romper filtros de datos históricos.
ESTADOS_VALIDOS = [
    "Borrador", "Pendiente de aprobación", "En aprobación", "Aprobada", "Rechazada",
    "En cotización", "En negociación", "Orden de Compra emitida", "Parcialmente atendida",
    "Recepción parcial", "Recepción completa", "Cerrada", "Cancelada", "Anulada"
]

# --- NUEVO (Requerimiento 10): helper de notificaciones automáticas (log mínimo) ---
def registrar_notificacion(cursor, req_code, tipo_evento, mensaje):
    cursor.execute("""
        INSERT INTO notificaciones_pendientes (requisicion_id, tipo_evento, mensaje)
        VALUES (%s, %s, %s)
    """, (req_code, tipo_evento, mensaje))

# --- NUEVO: helper reusado por la carga masiva Y por el alta individual de la
# Planilla Maestro de Requisiciones. Aplica el mismo criterio del FIX Bug #3:
# si la línea ya existe y cantidad_comprador ya fue auditada por Compras
# (difiere de cantidad_solicitada), se preserva; si nunca fue tocada, se sincroniza.
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

# --- FIX Bug #5 + NUEVO (Requerimiento 12): Roles y permisos reales ---
# Antes: "📥 Mapeador Masivo" estaba disponible para CUALQUIER usuario autenticado.
# Ahora: cada rol tiene un set explícito de menús permitidos. Solo Comprador y
# Administración ven el Mapeador Masivo (que puede sobrescribir toda la base).
ROLE_MENU_MAP = {
    "solicitante": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📝 Nueva Solicitud",
        "🔍 Buscador Rápido",
    ],
    "jefe de área": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📝 Nueva Solicitud",
        "⚖️ Cuadro Comparativo Masivo",
        "🔍 Buscador Rápido",
    ],
    "comprador": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📥 Mapeador Masivo",
        "⚖️ Cuadro Comparativo Masivo",
        "🛠️ Control de Compras",
        "📊 Dashboard Ejecutivo",
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    "gerencia": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "⚖️ Cuadro Comparativo Masivo",
        "📊 Dashboard Ejecutivo",
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    "directorio": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "⚖️ Cuadro Comparativo Masivo",
        "📊 Dashboard Ejecutivo",
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    "auditoría": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📊 Dashboard Ejecutivo",
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
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
        "🔔 Notificaciones",
    ],
    # Compatibilidad retroactiva: el rol "aprobador" ya está cargado en producción
    # (tabla usuarios) y no debe perder acceso mientras se migra a los roles nuevos.
    "aprobador": [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "⚖️ Cuadro Comparativo Masivo",
        "📊 Dashboard Ejecutivo",
        "✅ Aprobar / Rechazar",
        "🔍 Buscador Rápido",
    ],
}

opciones_permitidas = ROLE_MENU_MAP.get(
    st.session_state.user_role,
    ["🏢 Estructura Organizacional", "🤝 Gestión de Proveedores"]  # default mínimo seguro para roles no reconocidos
)

opcion_menu = st.sidebar.radio("Seleccione una sección:", opciones_permitidas)

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Filtros Gerenciales")
# --- FIX Bug #1 (mejora): el listado de estados ya no es una lista fija hardcodeada.
# Se fusiona ESTADOS_VALIDOS (Requerimiento 5) con los valores que ya existan
# realmente en la base (legado), para que el filtro nunca quede desactualizado
# ni oculte datos históricos con nomenclatura anterior.
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
        # --- FIX Bug #4: faltaban flexibility_score, financial_health_score y general_notes ---
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

    # --- FIX Bug #2 + NUEVO (Requerimiento 4 y recomendado de presupuesto) ---
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

    with t_items:
        st.markdown("#### ➕ Cargar un ítem")
        with st.form("form_individual_item"):
            fi_codigo = st.text_input("Código")
            fi_desc = st.text_input("Descripción estándar")
            fi_unidad = st.text_input("Unidad de medida")
            if st.form_submit_button("Guardar Ítem"):
                if not fi_codigo.strip():
                    st.error("El código es obligatorio.")
                else:
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                INSERT INTO items (codigo, descripcion_estandar, unidad_medida)
                                VALUES (%s, %s, %s) ON CONFLICT (codigo) DO UPDATE SET
                                descripcion_estandar=EXCLUDED.descripcion_estandar, unidad_medida=EXCLUDED.unidad_medida
                            """, (clean_id(fi_codigo), fi_desc.strip(), fi_unidad.strip()))
                    st.success(f"Ítem {fi_codigo} guardado.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 📊 Carga masiva por Excel")
        cols_i = ['codigo', 'descripcion_estandar', 'unidad_medida']
        ejemplo_i = [['IT-0001', 'Guantes de nitrilo talla M', 'Caja x100']]
        st.download_button("📥 Descargar Plantilla Ejemplo (Ítems)", generar_excel_descarga(cols_i, ejemplo_i), "ejemplo_items.xlsx", "application/vnd.ms-excel")
        up_i = st.file_uploader("Subir planilla del catálogo", type=["xlsx", "csv"], key="i_up")
        if up_i:
            df = pd.read_excel(up_i) if up_i.name.endswith('xlsx') else pd.read_csv(up_i)
            if st.button("🚀 Sincronizar Catálogo de Ítems"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df.iterrows():
                            cursor.execute("""
                                INSERT INTO items (codigo, descripcion_estandar, unidad_medida)
                                VALUES (%s, %s, %s) ON CONFLICT (codigo) DO UPDATE SET
                                descripcion_estandar=EXCLUDED.descripcion_estandar, unidad_medida=EXCLUDED.unidad_medida
                            """, (clean_id(r['codigo']), r['descripcion_estandar'], r['unidad_medida']))
                st.success("Catálogo maestro actualizado sin duplicados.")

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

    # --- NUEVO Bug #2: carga masiva de areas_emails (mismo patrón que t_prov) ---
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

    # --- NUEVO (Requerimiento 4): Empresas Compradoras (16 RUC) ---
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

    # --- NUEVO (recomendado): Presupuestos por Área ---
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
                        # --- FIX Bug #3: ya NO se hace DELETE masivo de requisitions_detalles.
                        # El DELETE previo pisaba cantidad_comprador ya auditada por Compras.
                        # Ahora se hace UPSERT línea por línea vía upsert_detalle_linea().
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
    # --- FIX Bug #1: se aplica status_filter para acotar qué requisiciones se gestionan aquí ---
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
        df_detalles_plantilla = pd.read_sql_query(
            "SELECT item_codigo, narrativa_solicitante, cantidad_solicitada FROM requisitions_detalles WHERE requisicion_id = %(req)s",
            get_engine(),
            params={"req": c_req_code}
        )

        if not df_detalles_plantilla.empty:
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
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df_q.iterrows():
                            cursor.execute("""
                                INSERT INTO budgets (req_code, provider_ruc, price, payment_terms_days, delivery_time_days)
                                VALUES (%s, %s, %s, %s, %s)
                            """, (c_req_code, clean_id(r['proveedor_ruc']), float(r['precio_total_usd']), safe_int(r['plazo_pago_dias']), safe_int(r['tiempo_entrega_dias'])))

                        # --- FIX: el flujo quedaba huérfano porque nunca se asignaba
                        # aprobador_actual. Se busca al primer aprobador de la cadena
                        # (secuencia_orden = 1) y se lo asigna explícitamente; sin esto,
                        # la bandeja de "Aprobar / Rechazar" nunca mostraba la requisición
                        # a nadie, porque esa pantalla filtra por aprobador_actual = email.
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

                        # NUEVO (Requerimiento 10): notificación mínima de evento
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
    # --- FIX Bug #1: se aplica status_filter a la query base del dashboard ---
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

        # --- NUEVO (Requerimiento 11): Reportes e indicadores ampliados ---
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

        # Órdenes vencidas: fecha_comprometida ya pasada y sin recepción completa
        if 'fecha_comprometida' in df_db.columns:
            fechas_compr = pd.to_datetime(df_db['fecha_comprometida'], errors='coerce').dt.date
            vencidas_mask = fechas_compr.notna() & (fechas_compr < hoy)
            st.metric("📌 Órdenes con Fecha Comprometida Vencida", int(vencidas_mask.sum()))

        # Exportación real a Excel (Requerimiento 11)
        output_rep = io.BytesIO()
        with pd.ExcelWriter(output_rep, engine='xlsxwriter') as writer:
            df_db.drop(columns=['data_aprova_dt'], errors='ignore').to_excel(writer, index=False, sheet_name='Reporte Requisiciones')
        st.download_button("📥 Exportar Reporte Completo a Excel", output_rep.getvalue(), "reporte_requisiciones.xlsx", "application/vnd.ms-excel")

        # --- NUEVO (recomendado): Alertas de compras duplicadas ---
        st.markdown("---")
        st.subheader("⚠️ Alertas de Posibles Compras Duplicadas")
        df_dupes = pd.read_sql_query("""
            SELECT item_codigo, requisicion_id, COUNT(*) OVER (PARTITION BY item_codigo) AS repeticiones
            FROM requisitions_detalles
        """, get_engine())
        df_dupes_flag = df_dupes[df_dupes['repeticiones'] > 1]
        if not df_dupes_flag.empty:
            st.dataframe(df_dupes_flag, use_container_width=True)
        else:
            st.caption("No se detectaron ítems solicitados en múltiples requisiciones.")

        st.markdown("---")
        st.subheader("🛠️ Auditoría y Modificación de Cantidades por Compras")

        select_req = st.selectbox("Seleccione el código de orden a auditar cantidades:", df_db['req_code'].unique())

        df_detalles = pd.read_sql_query(
            "SELECT id, item_codigo, narrativa_solicitante, cantidad_solicitada, cantidad_comprador FROM requisitions_detalles WHERE requisicion_id = %(req)s",
            get_engine(),
            params={"req": select_req}
        )

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

# 6. APROBAR / RECHAZAR (NUEVO — Requerimiento 7, flujo de aprobación real)
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

        col_ap, col_re = st.columns(2)

        with col_ap:
            st.markdown("**Aprobar**")
            if st.button("✅ Aprobar Requisición"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT secuencia_aprobacion_actual FROM requisitions WHERE req_code = %s", (req_a_resolver,))
                        seq_actual = cursor.fetchone()[0]

                        # Busca el siguiente aprobador en la cadena según secuencia_orden
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
                            # Era el último aprobador de la cadena
                            cursor.execute("""
                                UPDATE requisitions
                                SET situacao_solici = 'Aprobada'
                                WHERE req_code = %s
                            """, (req_a_resolver,))
                            nuevo_estado = 'Aprobada'

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

# 7. NUEVA SOLICITUD (NUEVO — Requerimiento 2)
elif opcion_menu == "📝 Nueva Solicitud":
    st.subheader("Crear Nueva Solicitud de Compra")
    st.caption("Una vez enviada, la solicitud queda bloqueada para el Solicitante: cualquier cambio "
               "(cantidades, ítems, cancelación) debe canalizarse a través de Compras (ver 'Control de Compras').")

    with st.form("form_nueva_solicitud"):
        area_sol = st.text_input("Área / Centro de Costo")
        items_disponibles = pd.read_sql_query("SELECT codigo, descripcion_estandar FROM items", get_engine())
        item_sol = st.selectbox("Ítem", items_disponibles['codigo'].tolist() if not items_disponibles.empty else [])
        cantidad_sol = st.number_input("Cantidad solicitada", min_value=1, value=1)
        narrativa_sol = st.text_area("Descripción / narrativa de la necesidad")

        if st.form_submit_button("📨 Enviar Solicitud"):
            if not item_sol:
                st.error("No hay ítems cargados en el catálogo todavía.")
            else:
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        # NUEVO (recomendado): numeración automática de solicitudes
                        cursor.execute("SELECT req_code FROM requisitions ORDER BY req_code DESC LIMIT 1")
                        ultimo = cursor.fetchone()
                        try:
                            siguiente_num = int(ultimo[0]) + 1 if ultimo and str(ultimo[0]).isdigit() else 100000
                        except (ValueError, TypeError):
                            siguiente_num = 100000
                        nuevo_req_code = str(siguiente_num)

                        cursor.execute("""
                            INSERT INTO requisitions (req_code, situacao_solici, pedido, data_solicita, analista_email, area_name)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (nuevo_req_code, 'Borrador', '0', datetime.now().date(),
                              st.session_state.user_email, area_sol or "Pendiente de Clasificación"))

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

# 8. CONTROL DE COMPRAS (NUEVO — Requerimiento 3, 4, 5, 9 y recepción/fechas)
elif opcion_menu == "🛠️ Control de Compras":
    st.subheader("Control Exclusivo de Compras sobre Requisiciones")
    ctrl_req_code = st.text_input("Código de Requisición a intervenir", "14660", key="ctrl_req")

    tab_items, tab_prov_resp, tab_empresa, tab_estado, tab_consolida, tab_docs, tab_recepcion = st.tabs([
        "📦 Ítems", "🤝 Proveedor / Responsable", "🏦 Empresa Compradora",
        "🔄 Estado", "🔀 Consolidar / Dividir OC", "📎 Documentos", "📥 Recepción"
    ])

    # --- Req. 3: eliminar / agregar / sustituir / modificar especificaciones ---
    with tab_items:
        df_det_ctrl = pd.read_sql_query(
            "SELECT id, item_codigo, narrativa_solicitante, cantidad_solicitada, cantidad_comprador FROM requisitions_detalles WHERE requisicion_id = %(req)s",
            get_engine(), params={"req": ctrl_req_code}
        )
        st.dataframe(df_det_ctrl, use_container_width=True)

        accion_item = st.selectbox("Acción sobre ítems", [
            "Agregar ítem adicional", "Eliminar ítem", "Sustituir por equivalente", "Modificar especificaciones técnicas"
        ])

        if accion_item == "Agregar ítem adicional":
            with st.form("form_agregar_item"):
                df_catalogo = pd.read_sql_query("SELECT codigo, descripcion_estandar FROM items", get_engine())
                nuevo_codigo = st.selectbox("Ítem del catálogo", df_catalogo['codigo'].tolist() if not df_catalogo.empty else [])
                nueva_cant = st.number_input("Cantidad", min_value=1, value=1)
                motivo_add = st.text_area("Motivo de la adición (obligatorio)")
                if st.form_submit_button("➕ Agregar Ítem"):
                    if not motivo_add.strip():
                        st.error("Debe indicar un motivo.")
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
                    id_a_borrar = st.selectbox("Línea a eliminar (ID)", df_det_ctrl['id'].tolist())
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
                with st.form("form_sustituir_item"):
                    id_a_sustituir = st.selectbox("Línea a sustituir (ID)", df_det_ctrl['id'].tolist())
                    df_catalogo2 = pd.read_sql_query("SELECT codigo, descripcion_estandar FROM items", get_engine())
                    nuevo_codigo_sust = st.selectbox("Nuevo ítem equivalente", df_catalogo2['codigo'].tolist() if not df_catalogo2.empty else [])
                    motivo_sust = st.text_area("Motivo de la sustitución (obligatorio)")
                    if st.form_submit_button("🔁 Sustituir Ítem"):
                        if not motivo_sust.strip():
                            st.error("Debe indicar un motivo.")
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
                    id_a_modif = st.selectbox("Línea a modificar (ID)", df_det_ctrl['id'].tolist())
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

    # --- Req. 3: cambiar proveedor asignado / reasignar responsable ---
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

    # --- Req. 4: Empresa Compradora + historial + control de presupuesto informativo ---
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

            # NUEVO (recomendado): control de presupuesto disponible — informativo en esta iteración
            if area_actual:
                df_presu = pd.read_sql_query(
                    "SELECT empresa_ruc, monto_asignado, monto_utilizado, periodo FROM presupuestos_area WHERE area_name = %(a)s ORDER BY periodo DESC",
                    get_engine(), params={"a": area_actual}
                )
                if not df_presu.empty:
                    df_presu['disponible'] = df_presu['monto_asignado'] - df_presu['monto_utilizado']
                    st.markdown("**💰 Presupuesto disponible para el área** (informativo — no bloquea la emisión de OC en esta versión)")
                    st.dataframe(df_presu, use_container_width=True)

    # --- Req. 5: Estados del proceso ---
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

    # --- Req. 3: consolidar varias solicitudes / dividir una solicitud ---
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
        df_det_dividir = pd.read_sql_query(
            "SELECT id, item_codigo FROM requisitions_detalles WHERE requisicion_id = %(req)s",
            get_engine(), params={"req": ctrl_req_code}
        )
        with st.form("form_dividir"):
            ids_grupo_2 = st.multiselect(
                "Ítems (ID) que van a la NUEVA OC (el resto queda en la OC original)",
                df_det_dividir['id'].tolist() if not df_det_dividir.empty else []
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

    # --- Req. 9: Gestión documental (MVP: BYTEA en Postgres, no Supabase Storage) ---
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

    # --- Recomendado: fechas comprometidas + recepción parcial/completa ---
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
        df_det_recep = pd.read_sql_query(
            "SELECT id, item_codigo, cantidad_comprador, cantidad_recibida, estado_recepcion FROM requisitions_detalles WHERE requisicion_id = %(req)s",
            get_engine(), params={"req": ctrl_req_code}
        )
        if not df_det_recep.empty:
            st.dataframe(df_det_recep, use_container_width=True)
            with st.form("form_recepcion"):
                id_recep = st.selectbox("Línea a recepcionar (ID)", df_det_recep['id'].tolist())
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

# 9. BUSCADOR RÁPIDO (NUEVO — Requerimiento 6, recomendado)
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
        df_busq_item = pd.read_sql_query(
            "SELECT codigo, descripcion_estandar FROM items WHERE descripcion_estandar ILIKE %(t)s OR codigo ILIKE %(t)s",
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

# 10. NOTIFICACIONES (NUEVO — Requerimiento 10, bitácora)
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
