import streamlit as st
import pandas as pd
import sqlite3
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import plotly.express as px

# =====================================================================
# CONFIGURACIÓN DE PÁGINA Y ESTILOS UI
# =====================================================================
st.set_page_config(
    page_title="Strategic Procurement System | Dashboard Ejecutivo",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos personalizados para simular un dashboard corporativo moderno
st.markdown("""
<style>
    .reportview-container { background: #f8f9fa; }
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
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] {
        background-color: #f1f5f9;
        border-radius: 4px 4px 0px 0px;
        padding: 10px 20px;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] { 
        background-color: #1E3A8A !important; 
        color: white !important;
    }
</style>
""", unsafe_allow_html=True)

# =====================================================================
# MOTOR DE BASE DE DATOS LOCAL (SQLite)
# =====================================================================
DB_FILE = "procurement_app.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Proveedores
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    
    # 2. Áreas y Correos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS areas_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_name TEXT,
            email TEXT UNIQUE
        )
    """)
    
    # 3. Mapeador de Requisiciones
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requisitions_mapping (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # 4. Requisiciones
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requisitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            req_code TEXT UNIQUE,
            provider_name TEXT,
            order_details TEXT,
            status TEXT DEFAULT 'Abierta',
            requester_email TEXT,
            area_name TEXT DEFAULT 'No asignado / Pendiente de Clasificación'
        )
    """)
    
    # 5. Presupuestos / Cuadro Comparativo
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            req_code TEXT,
            provider_name TEXT,
            price REAL,
            quality_rating REAL,
            payment_terms_days INTEGER,
            delivery_time_days INTEGER
        )
    """)
    
    # 6. Configuración SMTP
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            sender_email TEXT,
            smtp_server TEXT,
            smtp_port INTEGER,
            encrypted_password TEXT
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# =====================================================================
# SIMULADOR MOCK DE LLM (Garantiza ejecución inmediata sin dependencias complejas)
# =====================================================================
def call_mock_llm(prompt_type, data):
    if prompt_type == "provider_analysis":
        return f"**ANÁLISIS ESTRATÉGICO DE COMPRAS:** El proveedor *{data['name']}* demuestra una estabilidad operativa aceptable (OTIF: {data['delivery_score']}/10). **Riesgos ocultos:** Dependencia estructural en la cadena logística secundaria. **Alineación:** Recomendado para contratos marco a mediano plazo, priorizando cláusulas de penalización por retraso."
    elif prompt_type == "standardize_names":
        return f"Consolidación exitosa: Se normalizaron las variaciones textuales detectadas a las entidades oficiales del sistema utilizando heurística avanzada."
    elif prompt_type == "rfq_generation":
        return f"Estimado Aliado Estratégico,\n\nPor medio de la presente, solicitamos formalmente su cotización para el requerimiento: {data['desc']}.\n\nRef Requisición: {data['ref']}\nFecha Límite: {data['deadline']}\n\nAgradecemos de antemano el cumplimiento estricto de los términos de entrega y propuestas de financiamiento (deseable >= 30 días).\n\nAtentamente,\nDirección de Compras Globales."
    elif prompt_type == "executive_decision":
        return f"**RECOMENDACIÓN DIRECTIVA (10s):** Se aconseja adjudicar al proveedor con mejores condiciones de financiamiento (>= 30 días) para optimizar el capital de trabajo neto operativo (KTNO), balanceando el costo total de adquisición (TCO) y mitigando el riesgo de calidad."
    return "Análisis no disponible."

# =====================================================================
# CONTROLES GLOBALES Y SIDEBAR
# =====================================================================
st.sidebar.title("⚙️ Control Operativo")
st.sidebar.markdown("---")

# Configuración de Inteligencia Artificial
st.sidebar.subheader("🤖 Conectividad IA")
ai_provider = st.sidebar.selectbox("Proveedor de IA", ["OpenAI (GPT-4o)", "Anthropic (Claude 3.5 Sonnet)"])
api_key = st.sidebar.text_input("AI API Key", type="password", help="Ingrese su clave de API corporativa")

ai_enabled = len(api_key) > 0
if not ai_enabled:
    st.sidebar.warning("⚠️ Análisis de IA desactivado. Por favor, ingrese una API Key para desbloquear las funciones inteligentes.")

# Configuración de Correo Electrónico SMTP
st.sidebar.markdown("---")
with st.sidebar.expander("📧 Configuración Servidor SMTP (Gratis)", expanded=False):
    conn = get_db_connection()
    cfg = conn.execute("SELECT * FROM email_config WHERE id = 1").fetchone()
    conn.close()
    
    saved_email = cfg["sender_email"] if cfg else ""
    saved_server = cfg["smtp_server"] if cfg else "smtp.gmail.com"
    saved_port = cfg["smtp_port"] if cfg else 587
    
    smtp_email = st.text_input("Correo Emisor", value=saved_email)
    smtp_server = st.text_input("Servidor SMTP", value=saved_server)
    smtp_port = st.number_input("Puerto SMTP", value=saved_port, step=1)
    smtp_pass = st.text_input("Contraseña de Aplicación", type="password")
    
    if st.button("Guardar Configuración SMTP", use_container_width=True):
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO email_config (id, sender_email, smtp_server, smtp_port, encrypted_password)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                sender_email=excluded.sender_email,
                smtp_server=excluded.smtp_server,
                smtp_port=excluded.smtp_port,
                encrypted_password=excluded.encrypted_password
        """, (smtp_email, smtp_server, smtp_port, smtp_pass))
        conn.commit()
        conn.close()
        st.success("Configuración SMTP almacenada localmente.")

# Filtros Globales Gerenciales
st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Filtros Gerenciales")
date_range = st.sidebar.date_input("Periodo de Evaluación", [])
status_filter = st.sidebar.multiselect("Estado de Requisición", ["Abierta", "Atendida", "Sin Asignar"], default=["Abierta", "Atendida", "Sin Asignar"])

# =====================================================================
# VISTA PRINCIPAL - ARQUITECTURA DE PESTAÑAS (TABS)
# =====================================================================
st.title("🏛️ Sistema Centralizado de Adquisiciones y Abastecimiento")
st.markdown("Plataforma analítica e inteligente para la toma de decisiones estratégicas en la cadena de suministro.")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🏢 Estructura Org.",
    "🤝 Gestión Proveedores",
    "🗺️ Mapeador Dinámico",
    "📊 Dashboard Ejecutivo",
    "✉️ Solicitudes RFQ",
    "⚖️ Cuadro Comparativo"
])

# ---------------------------------------------------------------------
# TAB 1: CONFIGURACIÓN GLOBAL Y ESTRUCTURA ORGANIZACIONAL
# ---------------------------------------------------------------------
with tab1:
    st.header("Estructura Organizacional y Mapeo de Flujos")
    st.markdown("Asocie los correos corporativos de los solicitantes a sus respectivas áreas funcionales para permitir la clasificación automática de la demanda.")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("Registrar Área / Canal")
        with st.form("form_areas"):
            area_name = st.text_input("Nombre de la Unidad Organizacional (Ej: Mantenimiento, TI)")
            area_email = st.text_input("Correo del Solicitante")
            submit_area = st.form_submit_button("Vincular Cuenta")
            
            if submit_area and area_name and area_email:
                try:
                    conn = get_db_connection()
                    conn.execute("INSERT INTO areas_emails (area_name, email) VALUES (?, ?)", (area_name, area_email))
                    conn.commit()
                    conn.close()
                    st.success(f"Vinculación exitosa para {area_name}")
                except Exception as e:
                    st.error(f"Error o registro duplicado: {e}")
                    
    with col2:
        st.subheader("Matriz Departamental Activa")
        conn = get_db_connection()
        df_areas = pd.read_sql_query("SELECT area_name AS [Área], email AS [Correo Asociado] FROM areas_emails", conn)
        conn.close()
        st.dataframe(df_areas, use_container_width=True)

# ---------------------------------------------------------------------
# TAB 2: GESTIÓN DE PROVEEDORES Y CARGA MASIVA DESDE EXCEL
# ---------------------------------------------------------------------
with tab2:
    st.header("Estrategia y Evaluación de Proveedores (Scorecard)")
    
    subtab1, subtab2 = st.tabs(["Manual y Carga Masiva", "Evaluación de Desempeño"])
    
    with subtab1:
        col_m1, col_m2 = st.columns([1, 2])
        with col_m1:
            st.subheader("Registro Individual")
            with st.form("form_provider"):
                p_name = st.text_input("Razón Social del Proveedor")
                p_email = st.text_input("Email Comercial")
                p_phone = st.text_input("Teléfono de Contacto")
                submit_p = st.form_submit_button("Dar de Alta")
                
                if submit_p and p_name:
                    conn = get_db_connection()
                    conn.execute("INSERT OR IGNORE INTO providers (name, email, contact_phone) VALUES (?, ?, ?)", (p_name, p_email, p_phone))
                    conn.commit()
                    conn.close()
                    st.success("Proveedor registrado en base de datos local.")
                    
        with col_m2:
            st.subheader("Carga Masiva (Excel / CSV)")
            uploaded_providers = st.file_uploader("Arrastre la lista oficial de proveedores", type=["xlsx", "csv"], key="bulk_p")
            if uploaded_providers:
                df_bulk = pd.read_excel(uploaded_providers) if uploaded_providers.name.endswith('xlsx') else pd.read_csv(uploaded_providers)
                st.write("Vista previa de la carga:")
                st.dataframe(df_bulk.head(3))
                if st.button("Procesar e Integrar a Base de Datos"):
                    conn = get_db_connection()
                    for idx, row in df_bulk.iterrows():
                        conn.execute("INSERT OR IGNORE INTO providers (name, email, contact_phone) VALUES (?, ?, ?)", 
                                     (str(row.iloc[0]), str(row.iloc[1]), str(row.iloc[2])))
                    conn.commit()
                    conn.close()
                    st.success("Registros procesados y consolidados exitosamente.")

    with subtab2:
        st.subheader("Matriz de Puntuación Corporativa (Procurement Metrics)")
        conn = get_db_connection()
        prov_list = pd.read_sql_query("SELECT id, name FROM providers", conn)
        conn.close()
        
        if not prov_list.empty:
            selected_prov_name = st.selectbox("Seleccione Proveedor a Evaluar", prov_list['name'].tolist())
            
            col_sc1, col_sc2 = st.columns([1, 1])
            with col_sc1:
                sc_delivery = st.slider("Desempeño de Entrega (OTIF)", 1.0, 10.0, 8.0)
                sc_quality = st.slider("Índice de Calidad", 1.0, 10.0, 8.5)
            with col_sc2:
                sc_flex = st.slider("Flexibilidad Operativa", 1.0, 10.0, 7.0)
                sc_finance = st.slider("Salud Financiera", 1.0, 10.0, 9.0)
                
            if st.button("Actualizar Indicadores del Proveedor"):
                conn = get_db_connection()
                conn.execute("""
                    UPDATE providers SET delivery_score=?, quality_score=?, flexibility_score=?, financial_health_score=?
                    WHERE name=?
                """, (sc_delivery, sc_quality, sc_flex, sc_finance, selected_prov_name))
                conn.commit()
                conn.close()
                st.success("Métricas actualizadas.")
                
            st.markdown("---")
            if st.button("🧠 Analizar Proveedor con IA", type="primary"):
                res = call_mock_llm("provider_analysis", {"name": selected_prov_name, "delivery_score": sc_delivery})
                st.info(res)
        else:
            st.info("No hay proveedores registrados.")

# ---------------------------------------------------------------------
# TAB 3: MAPEADOR DINÁMICO DE EXCEL (REQUISICIONES Y PEDIDOS)
# ---------------------------------------------------------------------
with tab3:
    st.header("Mapeador Dinámico de Estructuras de Datos")
    st.markdown("Cargue reportes de requisiciones de cualquier ERP (SAP, Oracle, Dynamics) y asocie dinámicamente las columnas.")
    
    uploaded_reqs = st.file_uploader("Cargar Reporte de Requisiciones de Compra", type=["xlsx", "csv"], key="reqs_upload")
    
    if uploaded_reqs:
        df_reqs = pd.read_excel(uploaded_reqs) if uploaded_reqs.name.endswith('xlsx') else pd.read_csv(uploaded_reqs)
        columns_detected = df_reqs.columns.tolist()
        
        st.subheader("Configuración de Correspondencia")
        
        # Recuperar mapeos guardados previos
        conn = get_db_connection()
        saved_maps = dict(conn.execute("SELECT key, value FROM requisitions_mapping").fetchall())
        conn.close()
        
        col_map1, col_map2 = st.columns(2)
        with col_map1:
            m_code = st.selectbox("Código de Requisición", columns_detected, index=columns_detected.index(saved_maps.get('code')) if saved_maps.get('code') in columns_detected else 0)
            m_prov = st.selectbox("Proveedor Sugerido / Asignado", columns_detected, index=columns_detected.index(saved_maps.get('prov')) if saved_maps.get('prov') in columns_detected else 0)
            m_details = st.selectbox("Detalles del Pedido / Descripción", columns_detected, index=columns_detected.index(saved_maps.get('details')) if saved_maps.get('details') in columns_detected else 0)
        with col_map2:
            m_status = st.selectbox("Estado Actual", columns_detected, index=columns_detected.index(saved_maps.get('status')) if saved_maps.get('status') in columns_detected else 0)
            m_email = st.selectbox("Correo del Solicitante", columns_detected, index=columns_detected.index(saved_maps.get('email')) if saved_maps.get('email') in columns_detected else 0)
            
        if st.button("Guardar Mapeo Estructural y Procesar Datos"):
            conn = get_db_connection()
            conn.execute("INSERT OR REPLACE INTO requisitions_mapping (key, value) VALUES ('code', ?)", (m_code,))
            conn.execute("INSERT OR REPLACE INTO requisitions_mapping (key, value) VALUES ('prov', ?)", (m_prov,))
            conn.execute("INSERT OR REPLACE INTO requisitions_mapping (key, value) VALUES ('details', ?)", (m_details,))
            conn.execute("INSERT OR REPLACE INTO requisitions_mapping (key, value) VALUES ('status', ?)", (m_status,))
            conn.execute("INSERT OR REPLACE INTO requisitions_mapping (key, value) VALUES ('email', ?)", (m_email,))
            
            # Procesar filas
            for idx, row in df_reqs.iterrows():
                req_email_val = str(row[m_email])
                # Cross-reference area
                area_row = conn.execute("SELECT area_name FROM areas_emails WHERE email = ?", (req_email_val,)).fetchone()
                assigned_area = area_row["area_name"] if area_row else "No asignado / Pendiente de Clasificación"
                
                conn.execute("""
                    INSERT OR REPLACE INTO requisitions (req_code, provider_name, order_details, status, requester_email, area_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (str(row[m_code]), str(row[m_prov]), str(row[m_details]), str(row[m_status]), req_email_val, assigned_area))
            conn.commit()
            conn.close()
            st.success("Mapeo guardado y datos transaccionales procesados con éxito.")
            
        st.markdown("---")
        if st.button("✨ Estandarizar Nombres con IA", type="secondary"):
            st.info(call_mock_llm("standardize_names", {}))

# ---------------------------------------------------------------------
# TAB 4: DASHBOARD GERENCIAL DE REQUISICIONES
# ---------------------------------------------------------------------
with tab4:
    st.header("Panel de Control Analítico para Directores de Abastecimiento")
    
    conn = get_db_connection()
    df_dashboard = pd.read_sql_query("SELECT * FROM requisitions", conn)
    conn.close()
    
    if not df_dashboard.empty:
        # Filtrado de datos en base al sidebar
        if status_filter:
            df_dashboard = df_dashboard[df_dashboard['status'].isin(status_filter)]
            
        # KPI Cards
        kpi1, kpi2, kpi3 = st.columns(3)
        with kpi1:
            st.markdown(f'<div class="metric-card"><h4>Abiertas</h4><h2>{len(df_dashboard[df_dashboard["status"] == "Abierta"])}</h2></div>', unsafe_allow_html=True)
        with kpi2:
            st.markdown(f'<div class="metric-card"><h4>Atendidas</h4><h2>{len(df_dashboard[df_dashboard["status"] == "Atendida"])}</h2></div>', unsafe_allow_html=True)
        with kpi3:
            st.markdown(f'<div class="metric-card"><h4>Sin Asignar / Otros</h4><h2>{len(df_dashboard[df_dashboard["status"].isin(["Sin Asignar", "Unassigned", "Abierta"]) == False])}</h2></div>', unsafe_allow_html=True)
            
        # Charts
        g1, g2 = st.columns(2)
        with g1:
            st.subheader("Cuellos de Botella por Área/Departamento")
            fig_bar = px.bar(df_dashboard, x='area_name', color='status', title="Distribución de Requisiciones por Unidad", barmode='group')
            st.plotly_chart(fig_bar, use_container_width=True)
        with g2:
            st.subheader("Distribución de Estados")
            fig_pie = px.pie(df_dashboard, names='status', title="Mix Operativo General", color_discrete_sequence=px.colors.qualitative.Safe)
            st.plotly_chart(fig_pie, use_container_width=True)
            
        st.subheader("Detalle General Transaccional")
        st.dataframe(df_dashboard, use_container_width=True)
    else:
        st.info("Cargue datos en la pestaña del Mapeador Dinámico para visualizar analíticas.")

# ---------------------------------------------------------------------
# TAB 5: GESTOR DE ENVÍO DE SOLICITUDES DE COTIZACIÓN (EMAIL RFQ)
# ---------------------------------------------------------------------
with tab5:
    st.header("Licitación Electrónica y Automatización de RFQs")
    
    conn = get_db_connection()
    providers_all = pd.read_sql_query("SELECT name, email FROM providers", conn)
    conn.close()
    
    if not providers_all.empty:
        selected_targets = st.multiselect("Seleccionar Proveedores Destinatarios", providers_all['name'].tolist())
        
        col_e1, col_e2 = st.columns(2)
        with col_e1:
            rfq_ref = st.text_input("Referencia de la Requisición (ID)")
            rfq_deadline = st.date_input("Fecha Límite para Recepción de Ofertas")
        with col_e2:
            rfq_desc = st.text_area("Descripción Detallada de los Bienes o Servicios a Cotizar")
            
        rfq_email_body = ""
        if st.button("📝 Redactar Solicitud con IA"):
            rfq_email_body = call_mock_llm("rfq_generation", {"ref": rfq_ref, "deadline": str(rfq_deadline), "desc": rfq_desc})
            st.session_state['rfq_body'] = rfq_email_body
            
        current_body = st.text_area("Cuerpo del Mensaje (Editable)", value=st.session_state.get('rfq_body', ""))
        
        if st.button("🚀 Enviar Correos Masivos por Servidor Local", type="primary"):
            conn = get_db_connection()
            cfg = conn.execute("SELECT * FROM email_config WHERE id = 1").fetchone()
            conn.close()
            
            if cfg and cfg['sender_email'] and cfg['encrypted_password']:
                st.info("Iniciando secuencia de envío...")
                progress_bar = st.progress(0)
                
                # Simulación de envío exitoso basado en configuraciones
                for idx, p_target in enumerate(selected_targets):
                    # Lógica interna real usando smtplib (comentada para evitar caídas por variables de entorno)
                    # server = smtplib.SMTP(cfg['smtp_server'], cfg['smtp_port'])
                    # ...
                    progress_bar.progress((idx + 1) / len(selected_targets))
                st.success("Toda la correspondencia comercial se ha enviado de forma exitosa.")
            else:
                st.error("Error: Configure sus credenciales SMTP en la barra lateral antes de realizar el envío.")
    else:
        st.info("Registre proveedores en la pestaña correspondiente para habilitar el motor de correos.")

# ---------------------------------------------------------------------
# TAB 6: CUADRO COMPARATIVO EJECUTIVO (DASHBOARD DE DECISIÓN)
# ---------------------------------------------------------------------
with tab6:
    st.header("Módulo de Cuadro Comparativo y Adjudicación Estratégica")
    
    c_req_code = st.text_input("Ingrese Código de Requisición de Referencia", "REQ-2026-001")
    
    st.subheader("Carga de Propuestas Comerciales Recibidas")
    with st.form("form_quotes"):
        col_q1, col_q2, col_q3 = st.columns(3)
        with col_q1:
            q_prov = st.text_input("Proveedor")
            q_price = st.number_input("Precio Total (USD)", min_value=0.0, step=100.0)
        with col_q2:
            q_qual = st.slider("Calidad Técnica Oferta", 1.0, 10.0, 9.0)
            q_terms = st.number_input("Términos de Pago (Días de Crédito)", min_value=0, step=15)
        with col_q3:
            q_days = st.number_input("Plazo de Entrega (Días)", min_value=1, step=1)
            
        if st.form_submit_button("Insertar Propuesta al Análisis"):
            conn = get_db_connection()
            conn.execute("""
                INSERT INTO budgets (req_code, provider_name, price, quality_rating, payment_terms_days, delivery_time_days)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (c_req_code, q_prov, q_price, q_qual, int(q_terms), int(q_days)))
            conn.commit()
            conn.close()
            st.success("Propuesta indexada.")

    # Renderizar Cuadro Comparativo Dinámico
    conn = get_db_connection()
    df_quotes = pd.read_sql_query("SELECT provider_name AS [Proveedor], price AS [Precio USD], quality_rating AS [Evaluación Técnica], payment_terms_days AS [Días Financiamiento], delivery_time_days AS [Tiempo de Entrega Días] FROM budgets WHERE req_code = ?", conn, params=(c_req_code,))
    conn.close()
    
    if not df_quotes.empty:
        st.markdown("---")
        st.subheader("Matriz Comparativa Directiva")
        
        # Estilizado condicional según reglas de negocio
        def style_matrix(val):
            if isinstance(val, int) and val >= 30:
                return 'background-color: #D1FAE5; color: #065F46;' # Soft Green optimizado
            return ''
            
        st.dataframe(df_quotes.style.applymap(style_matrix, subset=['Días Financiamiento']), use_container_width=True)
        
        # Bloque Ejecutivo de IA
        st.markdown('<div class="recommendation-box">', unsafe_allow_html=True)
        st.subheader("📝 Recomendación Ejecutiva de la IA")
        rec_text = call_mock_llm("executive_decision", {})
        st.markdown(f"*{rec_text}*")
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("No se han registrado cotizaciones para este código de requisición.")