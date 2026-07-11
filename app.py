import streamlit as st
import pandas as pd
import psycopg2
import psycopg2.pool
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
# FIX #1: usamos psycopg2 (coherente con requirements.txt) con un pool
#          de conexiones cacheado, en vez de abrir/perder una conexión
#          nueva en cada interacción de Streamlit.
# =====================================================================
@st.cache_resource
def get_connection_pool():
    try:
        contrasena = st.secrets["database"]["password"]
    except KeyError:
        st.error("⚠️ Error Crítico: No se encontró la contraseña en el panel de Secrets de Streamlit.")
        raise
    return psycopg2.pool.SimpleConnectionPool(
        1, 10,
        host="aws-1-sa-east-1.pooler.supabase.com",
        port=5432,
        dbname="postgres",
        user="postgres.cotrwpikrtbwqlmbgixq",
        password=contrasena,
        sslmode="require",
    )


@contextmanager
def get_db_connection():
    """Toma una conexión prestada del pool y SIEMPRE la devuelve al terminar.
    Antes: `with psycopg2.connect(...)` abría una conexión nueva por cada
    llamada y nunca la cerraba (el `with` de psycopg2 solo maneja la
    transacción, no el cierre físico de la conexión). Con muchos usuarios
    o reruns de Streamlit, esto agotaba el límite de conexiones del pooler
    de Supabase."""
    pool_obj = get_connection_pool()
    conn = pool_obj.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool_obj.putconn(conn)


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


try:
    init_db()
except Exception as e:
    st.error(f"Error de conexión con Supabase: {e}. Verifica la configuración en Streamlit.")

# =====================================================================
# HELPERS DE LIMPIEZA DE DATOS
# FIX #3: Excel/pandas interpreta columnas de identificadores (RUC,
#          código de requisición) como float cuando no tienen texto,
#          lo que produce "80012345.0" en vez de "80012345".
# FIX #6: conversiones int() sin manejo de celdas vacías/NaN.
# =====================================================================
def clean_id(value):
    """Convierte un valor leído de Excel a texto limpio, sin sufijo '.0'."""
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def safe_int(value, default=0):
    """Convierte a int tolerando celdas vacías, NaN o texto no numérico."""
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


# SIMULADOR MOCK DE LLM
def call_mock_llm(prompt_type, data):
    if prompt_type == "executive_decision":
        return "**RECOMENDACIÓN DIRECTIVA (10s):** Se aconseja priorizar los flujos con plazos de financiamiento mayores a 30 días para proteger la caja operativa."
    return "Análisis no disponible."


# GENERADOR DE EXCEL EN MEMORIA PARA PLANTILLAS
def generar_excel_descarga(columnas, data=None):
    output = io.BytesIO()
    df = pd.DataFrame(data, columns=columnas) if data else pd.DataFrame(columns=columnas)
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Plantilla Modelo')
    return output.getvalue()


# =====================================================================
# SIDEBAR (CONTROLES GLOBALES)
# =====================================================================
st.sidebar.markdown("<h2 style='color:#1E3A8A; font-weight:700;'>🏛️ Panel de Control</h2>", unsafe_allow_html=True)
st.sidebar.markdown("---")

st.sidebar.subheader("🗂️ Módulos del Sistema")
opcion_menu = st.sidebar.radio(
    "Seleccione una sección:",
    [
        "🏢 Estructura Organizacional",
        "🤝 Gestión de Proveedores",
        "📥 Mapeador Masivo",
        "⚖️ Cuadro Comparativo Masivo",
        "📊 Dashboard Ejecutivo"
    ]
)

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Filtros Gerenciales")
status_filter = st.sidebar.multiselect("Estado General", ["Com Ordem", "Fechada", "Pendiente Aprobación"], default=["Com Ordem", "Fechada"])

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
            with get_db_connection() as conn:
                df_u = pd.read_sql_query('SELECT nombre AS "Nombre", email AS "Correo Institucional", rol AS "Rol Asignado" FROM usuarios', conn)
            if not df_u.empty:
                st.dataframe(df_u, use_container_width=True)
            else:
                st.info("No se registran usuarios cargados en el sistema.")
        except Exception as e:
            # FIX #5: ya no se oculta el error real; se muestra un detalle técnico
            st.info("No se registran usuarios cargados en el sistema.")
            with st.expander("Detalle técnico del error"):
                st.code(str(e))

    with tab_ruta:
        st.subheader("Flujo de Aprobadores Secuenciales en Cascada")
        try:
            with get_db_connection() as conn:
                df_aprob = pd.read_sql_query("""
                    SELECT nombre AS "Nombre Aprobador", email AS "Correo",
                           nivel_aprobacion AS "Escalón Jerárquico", secuencia_orden AS "Orden de Firma"
                    FROM usuarios WHERE rol = 'aprobador' ORDER BY secuencia_orden ASC
                """, conn)
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
        with get_db_connection() as conn:
            df_prov = pd.read_sql_query("""
                SELECT ruc AS "RUC", name AS "Razón Social", email AS "Email Comercial",
                       delivery_score AS "Score Entrega", quality_score AS "Score Calidad"
                FROM providers
            """, conn)
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

    t_usuarios, t_items, t_prov, t_reqs = st.tabs([
        "👥 Carga de Usuarios", "📦 Catálogo de Ítems", "🏢 Directorio Proveedores", "📑 Planilla Maestro Requisiciones"
    ])

    with t_usuarios:
        cols_u = ['nombre', 'email', 'rol', 'nivel_aprobacion', 'secuencia_orden']
        st.download_button("📥 Descargar Plantilla Ejemplo (Usuarios)", generar_excel_descarga(cols_u), "ejemplo_usuarios.xlsx", "application/vnd.ms-excel")
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
        cols_i = ['codigo', 'descripcion_estandar', 'unidad_medida']
        st.download_button("📥 Descargar Plantilla Ejemplo (Ítems)", generar_excel_descarga(cols_i), "ejemplo_items.xlsx", "application/vnd.ms-excel")
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
        cols_p = ['ruc', 'name', 'email', 'contact_phone']
        st.download_button("📥 Descargar Plantilla Ejemplo (Proveedores)", generar_excel_descarga(cols_p), "ejemplo_proveedores.xlsx", "application/vnd.ms-excel")
        up_p = st.file_uploader("Subir planilla de proveedores", type=["xlsx", "csv"], key="p_up")
        if up_p:
            df = pd.read_excel(up_p) if up_p.name.endswith('xlsx') else pd.read_csv(up_p)
            if st.button("🚀 Inyectar Directorio de Proveedores"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for _, r in df.iterrows():
                            # FIX #3: RUC y teléfono limpiados con clean_id para evitar "80012345.0"
                            cursor.execute("""
                                INSERT INTO providers (ruc, name, email, contact_phone)
                                VALUES (%s, %s, %s, %s) ON CONFLICT (ruc) DO UPDATE SET
                                name=EXCLUDED.name, email=EXCLUDED.email, contact_phone=EXCLUDED.contact_phone
                            """, (clean_id(r['ruc']), r['name'], r['email'], clean_id(r['contact_phone'])))
                st.success("Proveedores dados de alta de manera masiva.")

    with t_reqs:
        cols_r = ['Solicitação', 'Situação Solici', 'Pedido', 'Data Aprova', 'Data Solicita', 'E-mail Comp', 'E-mail Aprov', 'E-mail Solicit', 'Código Item', 'Narrativa Item', 'Cantidad Solicitada']
        st.download_button("📥 Descargar Estructura Maestro de Compras", generar_excel_descarga(cols_r), "plantilla_maestro.xlsx", "application/vnd.ms-excel")
        up_r = st.file_uploader("Arrastra el archivo maestro consolidado aquí", type=["xlsx", "csv"], key="r_up")
        if up_r:
            df = pd.read_excel(up_r) if up_r.name.endswith('xlsx') else pd.read_csv(up_r)
            if st.button("🚀 Ejecutar Motor de Integridad Correlativa (Cabecera + Detalle)"):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        # FIX #4: antes de re-insertar el detalle, borramos las líneas
                        # existentes de las requisiciones que vienen en este archivo,
                        # para que volver a subir el mismo Excel no duplique líneas.
                        codigos_unicos = [clean_id(v) for v in df['Solicitação'].unique()]
                        if codigos_unicos:
                            cursor.execute(
                                "DELETE FROM requisitions_detalles WHERE requisicion_id = ANY(%s)",
                                (codigos_unicos,)
                            )

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
                            cursor.execute("""
                                INSERT INTO requisitions_detalles (requisicion_id, item_codigo, narrativa_solicitante, cantidad_solicitada, cantidad_comprador)
                                VALUES (%s, %s, %s, %s, %s)
                            """, (req_code_val, clean_id(row['Código Item']), str(row['Narrativa Item']), cantidad, cantidad))
                st.success("Estructura transaccional mapeada con total integridad en Supabase.")

# 4. CUADRO COMPARATIVO MASIVO Y FLUJO DE APROBACIÓN JERÁRQUICA
elif opcion_menu == "⚖️ Cuadro Comparativo Masivo":
    c_req_code = st.text_input("Código de Requisición a Gestionar", "14660")

    col_acc1, col_acc2 = st.columns(2)
    with col_acc1:
        st.markdown("### Paso 1: Obtener Plantilla Estructurada")
        with get_db_connection() as conn:
            df_detalles_plantilla = pd.read_sql_query("SELECT item_codigo, narrativa_solicitante, cantidad_solicitada FROM requisitions_detalles WHERE requisicion_id = %s", conn, params=(c_req_code,))

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

                        cursor.execute("""
                            UPDATE requisitions SET
                            situacao_solici = 'Pendiente Aprobación',
                            secuencia_aprobacion_actual = 1
                            WHERE req_code = %s
                        """, (c_req_code,))
                st.success("Ofertas indexadas. La requisición avanzó a la ruta crítica del 'Aprobador 1'.")

    st.markdown("---")
    st.subheader(f"Matriz de Comparación Operativa para Requisición: {c_req_code}")
    with get_db_connection() as conn:
        df_quotes = pd.read_sql_query("""
            SELECT b.provider_ruc AS "RUC Proveedor", p.name AS "Razón Social",
                   b.price AS "Precio USD", b.payment_terms_days AS "Plazo Pago (Días)",
                   b.delivery_time_days AS "Tiempo Entrega (Días)"
            FROM budgets b
            LEFT JOIN providers p ON b.provider_ruc = p.ruc
            WHERE b.req_code = %s
        """, conn, params=(c_req_code,))

    if not df_quotes.empty:
        st.dataframe(df_quotes, use_container_width=True)
        st.markdown('<div class="recommendation-box">', unsafe_allow_html=True)
        st.subheader("💡 Análisis de Negociación IA")
        st.markdown(f"*{call_mock_llm('executive_decision', {})}*")
        st.markdown('</div>', unsafe_allow_html=True)

# 5. DASHBOARD EJECUTIVO
elif opcion_menu == "📊 Dashboard Ejecutivo":
    with get_db_connection() as conn:
        df_db = pd.read_sql_query("SELECT * FROM requisitions", conn)

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
        st.subheader("🛠️ Auditoría y Modificación de Cantidades por Compras")

        select_req = st.selectbox("Seleccione el código de orden a auditar cantidades:", df_db['req_code'].unique())

        with get_db_connection() as conn:
            df_detalles = pd.read_sql_query("SELECT id, item_codigo, narrativa_solicitante, cantidad_solicitada, cantidad_comprador FROM requisitions_detalles WHERE requisicion_id = %s", conn, params=(select_req,))

        if not df_detalles.empty:
            st.dataframe(df_detalles, use_container_width=True)

            with st.form("form_modificar_cantidades"):
                id_linea = st.number_input("ID de la línea a modificar", min_value=int(df_detalles['id'].min()), max_value=int(df_detalles['id'].max()))
                nueva_cantidad = st.number_input("Nueva Cantidad Autorizada por Compras", min_value=0, value=10)
                justificacion_compra = st.text_area("Justificación del Cambio (Campo Obligatorio)")

                if st.form_submit_button("Guardar Cambios de Auditoría"):
                    if not justificacion_compra.strip():
                        st.error("⚠️ Operación Bloqueada: Debe ingresar un motivo válido para alterar las cantidades originales del solicitante.")
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
