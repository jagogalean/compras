import streamlit as st
import pandas as pd
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
import plotly.express as px

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
# MOTOR DE BASE DE DATOS EN LA NUBE (CONEXIÓN POR DSN DIRECTO)
# =====================================================================
def get_db_connection():
    # REEMPLAZA ÚNICAMENTE EL TEXTO TU_CONTRASEÑA_REAL POR LA TUYA
    contrasena = "Rio!Cactus77-Nube*Tren-Limon"
    
    # Usamos el formato DSN estándar que evita el error de argumentos inesperados
    dsn = f"postgresql://postgres.cotrwpikrtbwqlmbgixq:{contrasena}@aws-1-sa-east-1.pooler.supabase.com:5432/postgres"
    return psycopg2.connect(dsn=dsn)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Tabla de Proveedores
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE,
            email TEXT,
            contact_phone TEXT,
            delivery_score REAL DEFAULT 10,
            quality_score REAL DEFAULT 10,
            flexibility_score REAL DEFAULT 10,
            financial_health_score REAL DEFAULT 10,
            general_notes TEXT
        )
    """)
    
    # 2. Áreas y Correos Solicitantes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS areas_emails (
            id SERIAL PRIMARY KEY,
            area_name TEXT,
            email TEXT UNIQUE
        )
    """)
    
    # 3. Flujo Jerárquico de Aprobadores (5 Niveles)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS approval_levels (
            id SERIAL PRIMARY KEY,
            approver_email TEXT UNIQUE,
            level_name TEXT,  
            sequence_order INTEGER
        )
    """)
    
    # 4. Tabla Maestra de Requisiciones (Estructura Correlativa Basada en 'Solicitação')
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requisitions (
            req_code TEXT PRIMARY KEY,          
            situacao_solici TEXT,               
            pedido TEXT,                        
            data_aprova DATE,                   
            data_solicita DATE,                 
            analista_email TEXT,                
            aprobador_actual TEXT,              
            narrativa_item TEXT,                
            narrativa_solicitacion TEXT,        
            area_name TEXT DEFAULT 'Pendiente de Clasificación'
        )
    """)
    
    # 5. Presupuestos / Cuadro Comparativo
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id SERIAL PRIMARY KEY,
            req_code TEXT,
            provider_name TEXT,
            price REAL,
            quality_rating REAL,
            payment_terms_days INTEGER,
            delivery_time_days INTEGER
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()

try:
    init_db()
except Exception as e:
    st.error(f"Error de conexión con Supabase: {e}. Verifica que tu contraseña sea correcta.")

# SIMULADOR MOCK DE LLM
def call_mock_llm(prompt_type, data):
    if prompt_type == "executive_decision":
        return "**RECOMENDACIÓN DIRECTIVA (10s):** Se aconseja priorizar los flujos con plazos de financiamiento mayores a 30 días para proteger la caja operativa."
    return "Análisis no disponible."

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
        "🗺️ Mapeador Dinámico",
        "📊 Dashboard Ejecutivo",
        "⚖️ Cuadro Comparativo"
    ]
)

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Filtros Gerenciales")
status_filter = st.sidebar.multiselect("Estado General", ["Com Ordem", "Fechada"], default=["Com Ordem", "Fechada"])

# =====================================================================
# RENDERIZADO LÓGICO
# =====================================================================
st.markdown(f"<div class='main-title'>{opcion_menu}</div>", unsafe_allow_html=True)
st.markdown("Plataforma analítica sincronizada en tiempo real con Supabase Postgres.")
st.markdown("---")

# 1. ESTRUCTURA ORGANIZACIONAL (MODIFICADA CON JERARQUÍA DE APROBADORES)
if opcion_menu == "🏢 Estructura Organizacional":
    tab_areas, tab_aprobadores = st.tabs(["Matriz de Áreas", "Jerarquía de Aprobadores (5 Niveles)"])
    
    with tab_areas:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.subheader("Vincular Área")
            with st.form("form_areas"):
                area_name = st.text_input("Nombre de la Unidad / Área")
                area_email = st.text_input("Correo Institucional del Área")
                submit_area = st.form_submit_button("Vincular Cuenta")
                
                if submit_area and area_name and area_email:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO areas_emails (area_name, email) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING", (area_name, area_email))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        st.success("Área vinculada correctamente.")
                    except Exception as e:
                        st.error(f"Error: {e}")
        with col2:
            st.subheader("Áreas Registradas")
            try:
                conn = get_db_connection()
                df_areas = pd.read_sql_query("SELECT area_name AS \"Área\", email AS \"Correo Asociado\" FROM areas_emails", conn)
                conn.close()
                st.dataframe(df_areas, use_container_width=True)
            except:
                st.info("Sin áreas en la nube.")

    with tab_aprobadores:
        col_ap1, col_ap2 = st.columns([1, 2])
        with col_ap1:
            st.subheader("Registrar Aprobador")
            with st.form("form_aprobadores"):
                ap_email = st.text_input("Correo del Aprobador (Como sale en la planilla)")
                ap_level = st.selectbox("Nivel de Aprobación", ["1er Aprobador", "2do Aprobador", "3er Aprobador", "4to Aprobador", "Aprobador Final"])
                ap_order = st.slider("Orden de Secuencia (1-5)", 1, 5, 1)
                submit_ap = st.form_submit_button("Guardar en Flujo")
                
                if submit_ap and ap_email:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO approval_levels (approver_email, level_name, sequence_order) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (approver_email) DO UPDATE SET level_name=EXCLUDED.level_name, sequence_order=EXCLUDED.sequence_order
                        """, (ap_email, ap_level, ap_order))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        st.success("Aprobador indexado al flujo.")
                    except Exception as e:
                        st.error(f"Error: {e}")
        with col_ap2:
            st.subheader("Ruta Crítica de Autorizaciones")
            try:
                conn = get_db_connection()
                df_aprob = pd.read_sql_query("SELECT approver_email AS \"Correo\", level_name AS \"Nivel Asignado\", sequence_order AS \"Secuencia\" FROM approval_levels ORDER BY sequence_order ASC", conn)
                conn.close()
                st.dataframe(df_aprob, use_container_width=True)
            except:
                st.info("Sin niveles configurados.")

# 2. GESTIÓN DE PROVEEDORES
elif opcion_menu == "🤝 Gestión de Proveedores":
    col_m1, col_m2 = st.columns([1, 2])
    with col_m1:
        st.subheader("Alta Individual")
        with st.form("form_provider"):
            p_name = st.text_input("Razón Social")
            p_email = st.text_input("Email Comercial")
            submit_p = st.form_submit_button("Registrar")
            if submit_p and p_name:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO providers (name, email) VALUES (%s, %s) ON CONFLICT(name) DO NOTHING", (p_name, p_email))
                conn.commit()
                cursor.close()
                conn.close()
                st.success("Proveedor guardado.")
    with col_m2:
        st.subheader("Proveedores Activos")
        try:
            conn = get_db_connection()
            df_prov = pd.read_sql_query("SELECT name AS \"Proveedor\", email AS \"Email\" FROM providers", conn)
            conn.close()
            st.dataframe(df_prov, use_container_width=True)
        except:
            st.info("No hay proveedores registrados.")

# 3. MAPEADOR DINÁMICO (LOGICA DE INTEGRIDAD CORRELAIVA)
elif opcion_menu == "🗺️ Mapeador Dinámico":
    st.subheader("Carga y Procesamiento de la Planilla Maestro de Compras")
    uploaded_reqs = st.file_uploader("Arrastra el archivo de compras aquí", type=["xlsx", "csv"])
    
    if uploaded_reqs:
        df_reqs = pd.read_excel(uploaded_reqs) if uploaded_reqs.name.endswith('xlsx') else pd.read_csv(uploaded_reqs)
        
        required_cols = ['Solicitação', 'Situação Solici', 'Pedido', 'Data Aprova', 'Data Solicita', 'E-mail Comp', 'E-mail Aprov', 'Narrativa ITE', 'Narrativa Solicitação', 'E-mail Solicit']
        missing_cols = [c for c in required_cols if c not in df_reqs.columns]
        
        if missing_cols:
            st.error(f"Faltan columnas esenciales en el archivo: {missing_cols}")
        else:
            st.success("Estructura de archivo validada exitosamente.")
            st.dataframe(df_reqs.head(3), use_container_width=True)
            
            if st.button("🚀 Sincronizar e Indexar Datos en Supabase (Sin Duplicados)"):
                conn = get_db_connection()
                cursor = conn.cursor()
                
                contador_nuevos = 0
                contador_actualizados = 0
                
                for idx, row in df_reqs.iterrows():
                    req_code_val = str(row['Solicitação']).strip()
                    
                    def parse_date(val):
                        try:
                            return pd.to_datetime(val).date()
                        except:
                            return None
                    
                    d_aprova = parse_date(row['Data Aprova'])
                    d_solicita = parse_date(row['Data Solicita'])
                    
                    cursor.execute("SELECT 1 FROM requisitions WHERE req_code = %s", (req_code_val,))
                    existe = cursor.fetchone()
                    if existe:
                        contador_actualizados += 1
                    else:
                        contador_nuevos += 1
                    
                    email_solicitante = str(row['E-mail Solicit']).strip()
                    cursor.execute("SELECT area_name FROM areas_emails WHERE email = %s", (email_solicitante,))
                    area_row = cursor.fetchone()
                    assigned_area = area_row[0] if area_row else "No asignado / Pendiente de Clasificación"
                    
                    cursor.execute("""
                        INSERT INTO requisitions (
                            req_code, situacao_solici, pedido, data_aprova, data_solicita, 
                            analista_email, aprobador_actual, narrativa_item, narrativa_solicitacion, area_name
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (req_code) DO UPDATE SET
                            situacao_solici = EXCLUDED.situacao_solici,
                            pedido = EXCLUDED.pedido,
                            data_aprova = EXCLUDED.data_aprova,
                            data_solicita = EXCLUDED.data_solicita,
                            analista_email = EXCLUDED.analista_email,
                            aprobador_actual = EXCLUDED.aprobador_actual,
                            narrativa_item = EXCLUDED.narrativa_item,
                            narrativa_solicitacion = EXCLUDED.narrativa_solicitacion,
                            area_name = EXCLUDED.area_name
                    """, (
                        req_code_val, str(row['Situação Solici']), str(row['Pedido']), d_aprova, d_solicita,
                        str(row['E-mail Comp']), str(row['E-mail Aprov']), str(row['Narrativa ITE']), str(row['Narrativa Solicitação']), assigned_area
                    ))
                
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"⚡ Sincronización Exitosa: {contador_nuevos} requisiciones nuevas agregadas, {contador_actualizados} estados actualizados por número de requisición.")

# 4. DASHBOARD EJECUTIVO (ALERTAS > 1 MES Y TRATAMIENTO)
elif opcion_menu == "📊 Dashboard Ejecutivo":
    conn = get_db_connection()
    df_db = pd.read_sql_query("SELECT * FROM requisitions", conn)
    df_levels = pd.read_sql_query("SELECT approver_email, level_name FROM approval_levels", conn)
    conn.close()
    
    if not df_db.empty:
        if status_filter:
            df_db = df_db[df_db['situacao_solici'].isin(status_filter)]
            
        df_db = df_db.merge(df_levels, left_on='aprobador_actual', right_on='approver_email', how='left')
        df_db['level_name'] = df_db['level_name'].fillna("Nivel No Asignado")
        
        def clasificar_pedido(row):
            if str(row['pedido']).strip() in ['0', '0.0', 'NaN', '']:
                return "Rechazado / Sin OC"
            return "Con Orden de Compra"
            
        df_db['Estado Pedido'] = df_db.apply(clasificar_pedido, axis=1)
        
        hoy = datetime.now().date()
        un_mes_atras = hoy - timedelta(days=30)
        
        def evaluar_alerta(val):
            if pd.isna(val): return "Ok"
            if isinstance(val, str):
                val_dt = pd.to_datetime(val).date()
            else:
                val_dt = val
            return "⚠️ RETRASADO (>1 Mes)" if val_dt < un_mes_atras else "Ok"
            
        df_db['Alerta Gestión'] = df_db['data_aprova'].apply(evaluar_alerta)
        
        kpi1, kpi2, kpi3 = st.columns(3)
        with kpi1:
            st.markdown(f'<div class="metric-card"><h4>Total de Requisiciones</h4><h2>{len(df_db)}</h2></div>', unsafe_allow_html=True)
        with kpi2:
            st.markdown(f'<div class="metric-card"><h4>Con Orden de Compra</h4><h2>{len(df_db[df_db["Estado Pedido"] == "Con Orden de Compra"])}</h2></div>', unsafe_allow_html=True)
        with kpi3:
            st.markdown(f'<div class="metric-card"><h4>Alertas por Retraso Extremo</h4><h2>{len(df_db[df_db["Alerta Gestión"] != "Ok"])}</h2></div>', unsafe_allow_html=True)
            
        g1, g2 = st.columns(2)
        with g1:
            fig_bar = px.bar(df_db, x='area_name', color='Estado Pedido', title="Estatus de Pedidos por Unidad Organizacional", barmode='group')
            st.plotly_chart(fig_bar, use_container_width=True)
        with g2:
            fig_aprov = px.pie(df_db, names='level_name', title="Distribución de Carga en la Cadena de Aprobación")
            st.plotly_chart(fig_aprov, use_container_width=True)
            
        st.subheader("Vista Operativa Detallada")
        
        def resaltar_alertas(row):
            if row['Alerta Gestión'] != "Ok":
                return ['background-color: #FEE2E2; color: #991B1B;'] * len(row)
            return [''] * len(row)
            
        df_ver = df_db[['req_code', 'situacao_solici', 'pedido', 'data_aprova', 'analista_email', 'level_name', 'narrativa_solicitacion', 'Alerta Gestión']]
        st.dataframe(df_ver.style.apply(resaltar_alertas, axis=1), use_container_width=True)
    else:
        st.info("No hay datos en la nube. Ve al Mapeador Dinámico para procesar tu planilla Excel.")

# 5. CUADRO COMPARATIVO
elif opcion_menu == "⚖️ Cuadro Comparativo":
    c_req_code = st.text_input("Código de Requisición a Analizar", "14660")
    
    with st.form("form_quotes"):
        col_q1, col_q2 = st.columns(2)
        with col_q1:
            q_prov = st.text_input("Nombre del Proveedor Postulante")
            q_price = st.number_input("Cotización Total (USD)", min_value=0.0)
        with col_q2:
            q_terms = st.number_input("Plazo de Pago (Días)", min_value=0)
            q_days = st.number_input("Plazo de Entrega Comercial (Días)", min_value=1)
            
        if st.form_submit_button("Indexar Oferta al Cuadro"):
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO budgets (req_code, provider_name, price, payment_terms_days, delivery_time_days)
                VALUES (%s, %s, %s, %s, %s)
            """, (c_req_code, q_prov, q_price, int(q_terms), int(q_days)))
            conn.commit()
            cursor.close()
            conn.close()
            st.success("Cotización asociada a la Requisición.")

    conn = get_db_connection()
    df_quotes = pd.read_sql_query("SELECT provider_name AS \"Proveedor\", price AS \"Precio USD\", payment_terms_days AS \"Días Financiamiento\", delivery_time_days AS \"Tiempo de Entrega Días\" FROM budgets WHERE req_code = %s", conn, params=(c_req_code,))
    conn.close()
    
    if not df_quotes.empty:
        st.dataframe(df_quotes, use_container_width=True)
        st.markdown('<div class="recommendation-box">', unsafe_allow_html=True)
        st.subheader("💡 Análisis Directivo del Sistema")
        st.markdown(f"*{call_mock_llm('executive_decision', {})}*")
        st.markdown('</div>', unsafe_allow_html=True)
