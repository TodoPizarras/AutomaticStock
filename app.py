from flask import Flask, request, render_template, redirect, url_for, jsonify
import pandas as pd
from flask_cors import CORS
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
CORS(app)

# --- CONFIGURACIÓN DE GOOGLE SHEETS ---
SCOPE = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file'
]

CREDS_FILE = 'credentials.json'
SHEET_ID = '17oJr8eCa_E-JEAKV8QSQzh5ze5un3k4lLCSNkO4WLNk'

creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
client = gspread.authorize(creds)

COL_CODIGO = 'Codigo'
COL_NOMBRE = 'Nombre'
COL_TIPO = 'Tipo'
COL_STOCK = 'Stock'
COL_COMPONENTES = 'Componentes'

def get_sheet_as_df():
    try:
        spreadsheet = client.open_by_key(SHEET_ID)
        sheet = spreadsheet.worksheet("StockMaster") 

        df = get_as_dataframe(sheet)

        df[COL_STOCK] = pd.to_numeric(df[COL_STOCK], errors='coerce').fillna(0).astype(int)

        return df
    except gspread.exceptions.SpreadsheetNotFound:
        raise Exception(f"No se encontró la hoja de cálculo con el ID '{SHEET_ID}'. Asegúrate de que el nombre sea correcto y que la hayas compartido con la cuenta de servicio.")
    except Exception as e:
        raise Exception(f"Error al leer la hoja de Google Sheets: {str(e)}")

def update_sheet_from_df(df):
    try:
        spreadsheet = client.open_by_key(SHEET_ID)
        sheet = spreadsheet.worksheet("StockMaster")

        set_with_dataframe(sheet, df, resize=True)
    except Exception as e:
        raise Exception(f"Error al escribir en la hoja de Google Sheets: {str(e)}")

@app.route('/')
def inicio():
    return render_template("inicio.html")

@app.route('/producto/<string:producto_id>')
def ver_producto(producto_id):
    try:
        df = get_sheet_as_df()
        producto = df[df[COL_CODIGO] == producto_id]

        if producto.empty:
            return "Producto no encontrado", 404

        nombre = producto.iloc[0][COL_NOMBRE]
        stock = producto.iloc[0][COL_STOCK]

        return render_template("producto.html", id=producto_id, nombre=nombre, stock=stock)
    except Exception as e:
        return f"Error al leer el producto: {str(e)}", 500

@app.route('/update/<string:producto_id>', methods=['POST'])
def actualizar_stock(producto_id):
    try:
        df = get_sheet_as_df()
        idx = df.index[df[COL_CODIGO] == producto_id].tolist()

        if not idx:
            return "Producto no encontrado", 404

        idx = idx[0]
        try:
            cantidad = int(request.form.get("cantidad", 1))
            if cantidad < 0:
                cantidad = 0
        except (ValueError, TypeError):
            cantidad = 1

        accion = request.form.get("accion")

        is_combo = df.at[idx, COL_TIPO].strip().lower() == 'combo'

        if accion == "sumar":
            df.at[idx, COL_STOCK] += cantidad
            if is_combo:
                componentes_str = str(df.at[idx, COL_COMPONENTES])
                if componentes_str and pd.notna(componentes_str):
                    codigos_componentes = [codigo.strip() for codigo in componentes_str.split(',')]
                    for comp_codigo in codigos_componentes:
                        comp_idx = df.index[df[COL_CODIGO] == comp_codigo].tolist()
                        if comp_idx:
                            df.at[comp_idx[0], COL_STOCK] += cantidad

        elif accion == "restar":
            df.at[idx, COL_STOCK] -= cantidad
            if is_combo:
                componentes_str = str(df.at[idx, COL_COMPONENTES])
                if componentes_str and pd.notna(componentes_str):
                    codigos_componentes = [codigo.strip() for codigo in componentes_str.split(',')]
                    for comp_codigo in codigos_componentes:
                        comp_idx = df.index[df[COL_CODIGO] == comp_codigo].tolist()
                        if comp_idx:
                            comp_stock = df.at[comp_idx[0], COL_STOCK]
                            df.at[comp_idx[0], COL_STOCK] = max(0, comp_stock - cantidad)

        update_sheet_from_df(df)
        return redirect(url_for('ver_producto', producto_id=producto_id))

    except Exception as e:
        return f"Error al actualizar el stock: {str(e)}", 500

@app.route('/masivo', methods=['GET', 'POST'])
def carga_masiva():
    try:
        df = get_sheet_as_df()
    except Exception as e:
        return f"Error al conectar con la base de datos: {e}", 500
        
    df_prod = df.loc[df[COL_TIPO] == "Producto"]
    product_ids = df_prod[COL_CODIGO].tolist()

    if request.method == 'POST':
        try:
            data = request.get_json()
            
            if not isinstance(data, list):
                return jsonify({"error": "Formato de datos inválido. Se esperaba una lista de productos."}), 400

            actualizacion_correcta = []
            error_actualizacion = []

            for item in data:
                producto_id = item.get('producto_id')
                cantidad_recibida = item.get('cantidad_recibida')

                if producto_id is None or not isinstance(cantidad_recibida, (int, float)) or cantidad_recibida < 0:
                    error_actualizacion.append(f"Datos inválidos para un producto (Codigo: {producto_id}, Cantidad: {cantidad_recibida}).")
                    
                    continue

                idx = df.index[df['Codigo'] == producto_id].tolist()

                if idx:
                    stock_actual = df.at[idx[0], 'Stock']
                    df.at[idx[0], 'Stock'] = max(0, stock_actual + cantidad_recibida)
                    actualizacion_correcta.append(f"Stock de {producto_id} actualizado a {df.at[idx[0], 'Stock']}")
                else:
                    error_actualizacion.append(f"Producto con Codigo '{producto_id}' no encontrado.")
            
            update_sheet_from_df(df)

            response = {
                "message": "Carga completada con algunas fallas." if error_actualizacion else "Carga de stock exitosa!",
                "successful": actualizacion_correcta,
                "failed": error_actualizacion
            }

            return jsonify(response), 200

        except Exception as e:
            return jsonify({"error": f"Ocurrió un error interno al procesar la carga: {str(e)}"}), 500

    return render_template("carga_masiva.html", product_ids=product_ids)

@app.route('/api/stock', methods=["GET"])
def get_stock_data():
    try:
        df = get_sheet_as_df()

        df_cleaned = df.dropna(how='all')
        df_filled = df_cleaned.fillna('')
        
        data = df_filled.to_dict('records')
        
        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": f"Error al conectar con la base de datos: {e}"}), 500

@app.route('/stock', methods=["GET"])
def stock_view():
    return render_template('stock.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)