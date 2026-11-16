"""Genera presentacion_MADeCa.html (raíz del proyecto): presentación comercial para clientes, un solo archivo con las
capturas dentro (WebP en base64), para enviarla o abrirla sin conexión. Mismo diseño que las de MAKeeping y MASupport.

Uso:  python gen_presentacion.py          (necesita Pillow)
PDF:  msedge --headless=new --no-pdf-header-footer --print-to-pdf=presentacion_MADeCa.pdf presentacion_MADeCa.html
Capturas: carpeta capturas/ = las de masoft.es (MASOFT\\WEB\\MASOFT\\assets\\madeca\\v04 y deca.webp; demo con datos
ficticios «Demo SL»; la revisión muestra el albarán del simulador ficticio TransCanarias).
Textos sacados de madeca.jsx de la web. Referencias normativas comprobadas en el BOE el 09/10/2026 (Orden FOM/2861/2012,
LOTT arts. 141.17 y 143.1.d, Ley 9/2025 DT 8.ª, BOE-A-2026-12784). No añadir cifras ni plazos sin fuente oficial.
Sin precios. Lo que no está comprobado con instalaciones reales (SAP Business One, Holded) se dice así.
"""
import base64
import io
import pathlib
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("Falta Pillow: pip install pillow")

AQUI = pathlib.Path(__file__).resolve().parent
CAPT = AQUI / "capturas"
SALIDA = AQUI.parent.parent / "presentacion_MADeCa.html"
FECHA = "Noviembre 2026"


def img(nombre: str, ancho: int) -> str:
    ruta = CAPT / f"{nombre}.webp"
    if not ruta.is_file():
        sys.exit(f"Falta la captura {ruta}")
    with Image.open(ruta) as im:
        if im.width <= ancho:  # ya es WebP del tamaño justo: tal cual, sin volver a comprimir
            return "data:image/webp;base64," + base64.b64encode(ruta.read_bytes()).decode()
        im = im.convert("RGB").resize((ancho, round(im.height * ancho / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=86, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def web(nombre: str, url: str) -> str:
    return (f'<figure class="navegador"><div class="barra"><i></i><i></i><i></i><span>{url}</span></div>'
            f'<img src="{img(nombre, 1440)}" alt=""></figure>')


def documento(nombre: str, clase: str = "") -> str:
    return f'<figure class="documento {clase}"><img src="{img(nombre, 800)}" alt=""></figure>'


# Icono de la app (app/static/favicon.svg): M blanca sobre azul marino
LOGO = ('<svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" fill="#1a3a5c"/>'
        '<path d="M15 46V18h7l10 15 10-15h7v28h-7V30l-10 14-10-14v16z" fill="#fff"/></svg>')

URL = "madeca.suempresa.es"


def puntos(*items: tuple[str, str]) -> str:
    return '<ul class="puntos">' + "".join(f"<li><b>{t}</b><span>{d}</span></li>" for t, d in items) + "</ul>"


def diapo(contenido: str, clase: str = "") -> str:
    return f'<section class="diapo {clase}">{contenido}</section>'


def cabecera(etiqueta: str, titulo: str, sub: str = "") -> str:
    return (f'<header class="cab"><span class="etiqueta">{etiqueta}</span><h2>{titulo}</h2>'
            + (f"<p>{sub}</p>" if sub else "") + "</header>")


def tarjetas(items, clase: str = "") -> str:
    return f'<div class="rejilla3 {clase}">' + "".join(f'<div class="tarjeta"><h3>{h}</h3><p>{p}</p></div>' for h, p in items) + "</div>"


def franja(*t: str) -> str:
    return '<div class="franja">' + "".join(f"<span>{x}</span>" for x in t) + "</div>"


def lado(nombre: str, url: str, *pts, corta: bool = False) -> str:
    return f'<div class="dos"><div class="grande{" corta" if corta else ""}">{web(nombre, url)}</div>{puntos(*pts)}</div>'


def pareja(a: tuple[str, str, str], b: tuple[str, str, str]) -> str:
    return ('<div class="dos-igual">' + "".join(f'<div>{web(n, u)}<p class="pie">{t}</p></div>' for n, u, t in (a, b)) + "</div>")


def construir() -> str:
    d = []
    # 1. Portada
    d.append(diapo(f"""
      <div class="portada-texto">
        <div class="marca">{LOGO}<span>MADeCa</span></div>
        <h1>El DeCA de cada transporte,<br><em>listo antes de que el camión salga</em></h1>
        <p class="lema">El documento de control del transporte de mercancías por carretera ya es obligatoriamente electrónico.
        MADeCa lo genera a partir de los albaranes que su empresa ya tiene, con su código QR para la inspección,
        sin cambiar su forma de trabajar.</p>
        <p class="firma">Masoft.es · {FECHA}</p>
      </div>
      <div class="portada-img">{web("inicio", URL)}{documento("deca", "flota")}</div>
    """, "portada"))

    # 2. La obligación
    d.append(diapo(cabecera("La obligación", "Desde el 5 de octubre de 2026, cada transporte lleva su DeCA electrónico",
                            "Están obligados el cargador contractual (también agencias, transitarios y operadores logísticos) y el transportista efectivo.") +
                   tarjetas([
                       ("Electrónico de origen", "Un albarán en papel escaneado no sirve como DeCA: el documento debe nacer en formato electrónico."),
                       ("Visible en carretera", "La inspección debe poder consultarlo con un código QR o un enlace, sin usuario ni contraseña."),
                       ("Conservado un año", "Cargador y transportista deben conservarlo al menos un año, a disposición de la Inspección de Transporte."),
                   ], "roja") + """
      <div class="riesgo"><span>401 – 600 €</span><div><b>Multa por infracción grave</b><p>Cuando falta el documento de control o le
      faltan datos esenciales. Responden el cargador y el transportista; el cargador queda eximido si prueba que lo emitió,
      y MADeCa deja constancia de cada emisión.</p></div></div>
      <div class="destacado">MADeCa genera el DeCA a partir de los albaranes que ya tiene: <b>listo antes de que el camión salga</b>.</div>
      <p class="fuentes">Fuentes (BOE, textos consolidados): Ley 9/2025, disposición transitoria 8.ª · Orden FOM/2861/2012,
      arts. 2.1, 4, 7 y 9 · Ley 16/1987 (LOTT), arts. 141.17 y 143.1.d · Resolución de 5 de junio de 2026 (BOE-A-2026-12784).</p>"""))

    # 3. La solución
    d.append(diapo(cabecera("La solución", "Sus albaranes, convertidos en DeCA válidos",
                            "MADeCa emite, guarda y sirve a la inspección el documento de cada envío. Para cargadores, transportistas, agencias y operadores logísticos.") + """
      <div class="cifras">
        <div><span>QR</span><b>sin usuario ni contraseña</b><p>La inspección lo consulta al momento, en carretera</p></div>
        <div><span>1</span><b>año de conservación</b><p>Con enlace privado para la otra parte durante ese plazo</p></div>
        <div><span>0</span><b>DeCA sin revisar</b><p>Nada se emite sin que una persona lo confirme</p></div>
        <div><span class="txt">ERP · nube · NAS</span><p>Lee los albaranes de donde ya los tiene, también escaneos</p></div>
      </div>""" + tarjetas([
        ("Emitir", "Si su ERP ya genera el albarán en PDF, MADeCa le añade el QR. Si no, redacta el documento completo con los datos, comprobando NIF, matrículas y fechas."),
        ("Acompañar el viaje", "Copia en PDF con QR para el conductor, cambios en ruta sobre el mismo documento y cierre de la dirección pública siete días después de la entrega."),
        ("Conservar", "Un año de conservación, enlace privado para la otra parte durante ese plazo y constancia de la fecha y hora de cada emisión."),
    ])))

    # 4. Cómo funciona
    d.append(diapo(cabecera("Cómo funciona", "Cuatro pasos, uno solo es suyo",
                            "MADeCa recoge y prepara; su equipo revisa y confirma. Así la responsabilidad de los datos queda donde la pone el art. 7 de la Orden FOM/2861/2012.") + """
      <ol class="flujo">
        <li><b>Llega el albarán</b><small>ERP · nube · NAS · foto</small><span>MADeCa lo recoge de donde ya lo tiene: su ERP, una carpeta compartida, la nube o una foto del papel.</span></li>
        <li><b>Lee los datos</b><small>Extracción · OCR</small><span>Cargador, transportista, destinatario, origen, destino, mercancía, fecha y matrícula, también de escaneos.</span></li>
        <li class="suyo"><b>Usted confirma</b><small>Bandeja de revisión</small><span>Lo que falta, en rojo y a un clic de su campo, con el documento original al lado. Nada se emite sin revisión.</span></li>
        <li><b>DeCA con su QR</b><small>PDF · QR · enlace</small><span>Copia para el conductor, enlace privado para la otra parte y consulta inmediata para la inspección.</span></li>
      </ol>""" + f'<div class="ancha baja">{web("bandeja", URL + "/bandeja")}</div>'))

    # 5. Inicio
    d.append(diapo(cabecera("Inicio", "Lo pendiente, lo que está en ruta y lo que necesita atención") +
                   lado("inicio", URL + " · Inicio",
                        ("Pendientes de revisar", "Cuántos hay y cuántos tienen datos que faltan."),
                        ("En tránsito, emitidos y entregados", "Las cifras del mes de un vistazo."),
                        ("Avisos", "Certificados por caducar, borradores sin revisar y fuentes con error, también por correo a los administradores."),
                        ("Últimas expediciones", "Con su matrícula y su estado, a un clic."))))

    # 6. Revisión
    d.append(diapo(cabecera("Revisión", "No da por bueno ningún dato del que no esté seguro",
                            "Si un dato no aparece o no puede comprobarse, como un NIF cuyo dígito de control no cuadra, en lugar de suponerlo lo señala y pide su comprobación.") +
                   lado("revision", URL + " · Revisión del albarán",
                        ("Lo que falta, enlazado a su campo", "El campo queda en rojo hasta confirmarlo."),
                        ("El original, al lado", "Y recuadrado el lugar donde se leyó cada dato."),
                        ("Comprobación al escribir", "NIF, matrículas y fechas, con las mismas reglas que al emitir."),
                        ("Cada dato con su artículo", "Las secciones siguen el art. 6 de la Orden FOM/2861/2012."))))

    # 7. Bandeja
    d.append(diapo(cabecera("Bandeja de revisión", "Varios albaranes a la vez, sin perder el control",
                            "Todo lo que llega de las fuentes, de la API o subido en el panel espera aquí su confirmación.") +
                   lado("bandeja", URL + " · Bandeja de revisión",
                        ("Pendientes, emitidos y descartados", "Cada uno en su pestaña, con su número."),
                        ("Búsqueda y filtros", "Por documento, referencia, matrícula, cliente, NIF, origen, destino o fecha."),
                        ("Emitir los completos", "Varios a la vez, siempre con confirmación."),
                        ("Descartar", "Lo que no sirve pasa a «Descartados», sin borrarse."))))

    # 8. El DeCA
    d.append(diapo(cabecera("El DeCA", "Un documento completo, con su código QR") + f"""
      <div class="dos"><div class="doc-grande">{documento("deca")}</div>""" +
                   puntos(("Los datos del art. 6", "Cargador, transportista, destinatario, origen y destino, vehículo, mercancía y fecha del transporte."),
                          ("QR de verificación", "Acceso libre, sin usuario ni contraseña, conforme al Apartado Tercero de la Resolución."),
                          ("Copia para el conductor", "En PDF con su QR, en el móvil o en papel."),
                          ("Firma cuando hace falta", "Si el DeCA tiene finalidad contractual, MADeCa firma con el certificado de su empresa.")) + "</div>" +
                   franja("Fecha y hora de emisión en el propio documento", "Enlace privado para la otra parte durante un año",
                          "Dirección pública cerrada siete días después de la entrega")))

    # 9. Cambios en ruta
    d.append(diapo(cabecera("Durante el viaje", "Cambios en ruta sin cambiar de documento",
                            "Si cambia la matrícula o el destino, la modificación se añade al mismo PDF y los datos anteriores quedan marcados como no válidos.") +
                   lado("expedicion", URL + " · Expedición",
                        ("Mismo QR, misma dirección", "La inspección ve siempre la versión vigente."),
                        ("Motivo de cada cambio", "Fecha, hora, qué cambió y quién lo hizo."),
                        ("Actividad de la expedición", "Quién emitió, modificó o entregó cada DeCA y cuándo."),
                        ("Registro de accesos", "Cada consulta del documento queda anotada."))))

    # 10. Expediciones
    d.append(diapo(cabecera("Expediciones", "Todo lo emitido, encontrado al momento") +
                   pareja(("expediciones", URL + " · Expediciones", "Búsqueda por referencia, matrícula, cliente o NIF, periodos de fechas, estados con su número y exportación a Excel."),
                          ("nueva", URL + " · Nueva expedición", "Formulario por secciones del art. 6, con comprobación al escribir y sugerencias de clientes de DeCA anteriores."))))

    # 11. Historial
    d.append(diapo(cabecera("Historial", "Qué se ha gestionado, para quién y con qué vehículo",
                            "Estadísticas por día, semana, mes, trimestre, año o fechas a elegir, con la evolución del periodo.") +
                   pareja(("historial", URL + " · Historial", "Todo lo gestionado en el periodo elegido, con su evolución."),
                          ("historial_desglose", URL + " · Desglose", "Por destinatario, cargador, transportista, vehículo, provincia o mercancía. Pulse una fila y todo el historial se filtra por ella.")) +
                   franja("Expediciones e historial a Excel, con sus filtros y totales", "Cada cifra filtra al pulsarla")))

    # 12. Integraciones
    d.append(diapo(cabecera("Integraciones", "Se conecta con lo que ya usa",
                            "MADeCa revisa cada pocos minutos la fuente que elija y deja los albaranes nuevos en la bandeja de revisión.") +
                   lado("fuentes", URL + " · Fuentes de datos",
                        ("ERP", "Microsoft Dynamics 365 Business Central y Odoo. También SAP Business One y Holded, con la correspondencia de campos por verificar en cada instalación."),
                        ("Documentos en la nube", "SharePoint, OneDrive, Google Drive y Dropbox."),
                        ("Red de la empresa", "NAS por SFTP o el conector local, que lee los documentos dentro de su red y solo envía los datos."),
                        ("ERP sin API o a medida", "Carpeta compartida donde el ERP deja el PDF, o API para que su programa envíe los albaranes."))))

    # 13. Garantías
    d.append(diapo(cabecera("Garantías", "Cumplimiento y seguridad desde el diseño") +
                   tarjetas([
                       ("Revisión humana", "Su equipo confirma cada DeCA antes de emitirlo; la responsabilidad de los datos queda donde la pone la norma."),
                       ("Cada empresa, sus datos", "Accesos separados por empresa y por perfil: administración, operación y consulta."),
                       ("Claves protegidas", "Las credenciales de su ERP y de su nube se guardan cifradas y no se vuelven a mostrar."),
                       ("Cambios en ruta", "La modificación se añade al mismo documento: mismo QR, misma dirección."),
                       ("Constancia de cada emisión", "Cada DeCA queda registrado con su fecha y hora de emisión, la prueba que exime al cargador si el documento no va a bordo."),
                       ("En la nube o en su servidor", "Servicio gestionado por Masoft o instalación propia junto a su ERP y su NAS."),
                   ]) + franja("Orden FOM/2861/2012", "Resolución de 5 de junio de 2026 (BOE-A-2026-12784)", "Conservación de un año",
                               "Credenciales cifradas") +
                   '<div class="destacado">Nada se emite sin que una persona lo confirme: <b>MADeCa prepara, su equipo decide</b>.</div>'))

    # 14. Modalidades
    d.append(diapo(cabecera("Modalidades", "Donde mejor le encaje") +
                   tarjetas([
                       ("Servicio gestionado", "MADeCa funciona en un servidor gestionado por Masoft. Su equipo entra con el navegador, sin instalar nada."),
                       ("En su servidor", "Instalación propia junto a su ERP y su NAS: Linux, Windows 10/11 o Windows Server, máquina virtual o servidor dedicado. También sin conexión a internet durante la instalación."),
                       ("MADeCa Mini, gratuita", "La misma aplicación para un servidor propio, con tres límites: una empresa, 30 DeCA al día y tres vehículos. Sin soporte ni garantía."),
                   ]) + """
      <div class="cifras">
        <div><span>1</span><b>empresa</b><p>MADeCa Mini: una sola empresa por instalación</p></div>
        <div><span>30</span><b>DeCA al día</b><p>Lo que pase del límite espera en la bandeja</p></div>
        <div><span>3</span><b>vehículos</b><p>Hasta tres cabezas tractoras o camiones</p></div>
        <div><span class="txt">Sin límites</span><p>La versión completa, gestionada o en su servidor</p></div>
      </div>""" + '<div class="destacado">Le preparamos una demostración con <b>sus propios albaranes</b> y una propuesta según su volumen de envíos.</div>'))

    # 15. Preguntas
    d.append(diapo(cabecera("Preguntas frecuentes", "Lo que nos preguntan siempre") +
                   '<div class="rejilla2">' + "".join(f'<div class="tarjeta"><h3>{q}</h3><p>{a}</p></div>' for q, a in [
                       ("¿Tengo que cambiar de ERP?", "No. MADeCa lee de su ERP, de una carpeta o de sus documentos."),
                       ("¿Y si no tengo ERP?", "Puede subir sus albaranes o rellenar el formulario del panel."),
                       ("¿Vale escanear el albarán en papel?", "Como DeCA, no. Pero MADeCa lee el escaneo y genera un documento válido."),
                       ("¿Necesito certificado digital?", "Solo si el DeCA tiene finalidad contractual. En ese caso MADeCa firma con el certificado de su empresa."),
                       ("¿Quién está obligado?", "El cargador contractual (también agencias, transitarios y operadores logísticos) y el transportista efectivo."),
                       ("¿Qué transportes quedan fuera?", "Las mudanzas, la paquetería de pocos bultos, el traslado de vehículos averiados y los transportes que no necesitan autorización."),
                   ]) + "</div>"))

    # 16. Cierre
    d.append(diapo(f"""
      <div class="cierre">
        <div class="marca grande-marca">{LOGO}<span>MADeCa</span></div>
        <h2>Sus albaranes de siempre,<br>convertidos en DeCA válidos antes de salir</h2>
        <p>Le preparamos una demostración con sus propios albaranes.</p>
        <p class="firma">Desarrollado por Masoft.es</p>
      </div>""", "portada fin"))
    return "\n".join(d)


PLANTILLA = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MADeCa · Presentación</title>
<style>
:root{--marino:#1a3a5c;--marino2:#0f2740;--azul:#2563eb;--verde:#16a34a;--rojo:#dc2626;--marca:#e8563f;--texto:#132033;--suave:#5b6b80;
--fondo:#f4f6fa;--borde:#dde3ea}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#0b1626;font-family:"Segoe UI",Inter,Roboto,system-ui,sans-serif;color:var(--texto);overflow:hidden}
#escena{position:fixed;inset:0;display:flex;align-items:center;justify-content:center}
#lienzo{width:1600px;height:900px;position:relative;transform-origin:center center;flex:none}
.diapo{position:absolute;inset:0;background:var(--fondo);padding:56px 72px 64px;display:flex;flex-direction:column;gap:26px;
 opacity:0;visibility:hidden;transition:opacity .35s ease;overflow:hidden;border-radius:6px}
.diapo.activa{opacity:1;visibility:visible}
.diapo::after{content:"MADeCa · Masoft.es";position:absolute;left:72px;bottom:24px;font-size:14px;color:#94a3b8;letter-spacing:.04em}
.diapo.portada::after{display:none}
.cab .etiqueta{display:inline-block;font-size:14px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--azul);margin-bottom:10px}
.cab h2{font-size:42px;font-weight:600;line-height:1.1;color:var(--marino2)}
.cab p{font-size:20px;color:var(--suave);margin-top:10px;max-width:1380px;line-height:1.4}
.portada{background:radial-gradient(1200px 700px at 85% 20%,#24527f 0%,transparent 60%),linear-gradient(135deg,#0f2740,#1a3a5c 60%,#173252);
 color:#fff;flex-direction:row;align-items:center;gap:40px}
.portada-texto{flex:0 0 720px}
.marca{display:flex;align-items:center;gap:16px;font-size:30px;font-weight:600;letter-spacing:.01em}
.marca svg{width:58px;height:58px;border-radius:16px;box-shadow:0 0 0 2px rgba(255,255,255,.25)}
.portada h1{font-size:50px;font-weight:300;line-height:1.1;margin:38px 0 24px}
.portada h1 em{font-style:normal;font-weight:600;color:#93c5fd}
.lema{font-size:21px;line-height:1.5;color:#cbd5e1}
.firma{margin-top:40px;font-size:17px;color:#93a4bb;letter-spacing:.05em}
.portada-img{flex:1;position:relative;height:620px}
.portada-img .navegador{position:absolute;right:-160px;top:30px;width:860px;transform:perspective(1800px) rotateY(-9deg)}
.portada-img .movil.flota{position:absolute;left:-10px;bottom:-10px;width:230px}
.fin{justify-content:center;text-align:center}
.cierre{display:flex;flex-direction:column;align-items:center;gap:26px}
.cierre h2{font-size:48px;font-weight:300;line-height:1.18}
.cierre p{font-size:23px;color:#cbd5e1}
.grande-marca{font-size:40px}.grande-marca svg{width:80px;height:80px}
.navegador{background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 30px 60px -20px rgba(15,39,64,.45),0 0 0 1px rgba(15,39,64,.08)}
.navegador .barra{height:30px;background:#e8edf3;display:flex;align-items:center;gap:7px;padding:0 12px}
.navegador .barra i{width:11px;height:11px;border-radius:50%;background:#cbd3dd}
.navegador .barra i:nth-child(1){background:#f87171}.navegador .barra i:nth-child(2){background:#fbbf24}.navegador .barra i:nth-child(3){background:#34d399}
.navegador .barra span{margin-left:14px;font-size:13px;color:#64748b;background:#fff;border-radius:6px;padding:3px 14px;flex:1;max-width:420px}
.navegador img{display:block;width:100%}
.movil{background:#111;border-radius:34px;padding:9px;box-shadow:0 30px 50px -18px rgba(15,39,64,.5)}
.movil img{display:block;width:100%;border-radius:26px}
.dos{display:flex;gap:40px;align-items:flex-start;flex:1;min-height:0}
.dos .grande{flex:0 0 1000px}
.dos .grande .navegador img{max-height:560px;object-fit:cover;object-position:top}
.dos .grande.corta .navegador img{max-height:480px}
.ancha{flex:1;min-height:0;display:flex;justify-content:center;align-items:flex-start}
.ancha .navegador{width:1240px}.ancha .navegador img{max-height:520px;object-fit:cover;object-position:top}
.ancha.baja .navegador{width:1100px}.ancha.baja .navegador img{max-height:300px}
.dos-igual{display:flex;gap:36px;flex:1;align-items:flex-start}.dos-igual>div{flex:1}
.dos-igual .navegador img{max-height:440px;object-fit:cover;object-position:top}
.pie{font-size:18px;color:var(--suave);margin-top:16px;line-height:1.4}
.moviles{display:flex;gap:22px;align-items:flex-start;justify-content:center}
.moviles.mini .movil{width:205px}
.moviles.cuatro{flex:0 0 820px}.moviles.cuatro .movil{width:190px}
.oscuro{flex:1}.oscuro .navegador img{max-height:400px;object-fit:cover;object-position:top}
.puntos{list-style:none;display:flex;flex-direction:column;gap:22px;flex:1}
.puntos li{display:flex;flex-direction:column;gap:5px;padding-left:22px;border-left:4px solid var(--azul)}
.puntos b{font-size:22px;color:var(--marino2);font-weight:600}
.puntos span{font-size:18px;color:var(--suave);line-height:1.4}
.franja{display:flex;flex-wrap:wrap;gap:12px}
.franja span{background:#fff;border:1px solid var(--borde);border-radius:999px;padding:10px 20px;font-size:17px;color:var(--marino2)}
.franja span::before{content:"✓";color:var(--verde);font-weight:700;margin-right:9px}
.destacado{margin-top:auto;margin-bottom:30px;background:linear-gradient(135deg,#0f2740,#1a3a5c);color:#fff;border-radius:16px;padding:28px 40px;font-size:25px;font-weight:300;line-height:1.4}
.destacado b{font-weight:600;color:#93c5fd}
.rejilla3{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}
.rejilla2{display:grid;grid-template-columns:repeat(2,1fr);gap:20px}
.tarjeta{background:#fff;border:1px solid var(--borde);border-radius:14px;padding:26px 30px;border-top:5px solid var(--marino)}
.roja .tarjeta{border-top-color:var(--marca)}
.tarjeta h3{font-size:23px;font-weight:600;color:var(--marino2);margin-bottom:10px}
.tarjeta p{font-size:18px;line-height:1.45;color:var(--suave)}
.rejilla2 .tarjeta{padding:22px 28px}
.cifras{display:grid;grid-template-columns:repeat(4,1fr);gap:22px}
.cifras div{background:linear-gradient(135deg,#0f2740,#1a3a5c);color:#fff;border-radius:16px;padding:26px 28px;display:flex;flex-direction:column;gap:6px}
.cifras span{font-size:52px;font-weight:300;line-height:1;color:#93c5fd}
.cifras span.txt{font-size:38px;margin-bottom:6px}
.cifras b{font-size:20px;font-weight:600}.cifras p{font-size:16.5px;color:#cbd5e1;line-height:1.4}
.flujo{list-style:none;display:grid;grid-template-columns:repeat(4,1fr);gap:26px;counter-reset:f}
.flujo li{position:relative;background:#fff;border:1px solid var(--borde);border-radius:14px;padding:26px 24px;display:flex;flex-direction:column;gap:8px;counter-increment:f}
.flujo li::before{content:counter(f);width:42px;height:42px;border-radius:50%;background:var(--marino);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:20px;margin-bottom:6px}
.flujo li:not(:last-child)::after{content:"▶";position:absolute;right:-21px;top:40px;color:var(--azul);font-size:16px}
.flujo b{font-size:22px;color:var(--marino2)}.flujo small{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--azul);font-weight:700}
.flujo span{font-size:16.5px;color:var(--suave);line-height:1.45}
.tabla{background:#fff;border:1px solid var(--borde);border-radius:14px;overflow:hidden}
.tabla div{display:grid;grid-template-columns:300px 1fr;padding:16px 28px;border-bottom:1px solid var(--borde);align-items:center}
.tabla div:nth-child(even){background:#f8fafc}.tabla div:last-child{border-bottom:0}
.tabla b{font-size:20px;color:var(--marino2)}.tabla span{font-size:18px;color:var(--suave)}
.capas{display:flex;align-items:stretch;gap:18px}
.capas div{flex:1;background:#fff;border:1px solid var(--borde);border-radius:14px;padding:24px 28px;display:grid;grid-template-columns:auto 1fr;column-gap:18px;row-gap:4px;align-items:center}
.capas div span{grid-row:1/3;width:48px;height:48px;border-radius:50%;background:var(--marino);color:#fff;display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:700}
.capas div.fin{background:linear-gradient(135deg,#0f2740,#1a3a5c);color:#fff;border:0}.capas div.fin span{background:var(--verde)}
.capas b{font-size:22px}.capas p{font-size:17px;color:var(--suave)}.capas .fin p{color:#cbd5e1}
.capas i{font-style:normal;font-size:34px;color:var(--suave);align-self:center}
.pasos{list-style:none;display:grid;grid-template-columns:repeat(3,1fr);gap:22px;counter-reset:p}
.pasos li{background:#fff;border:1px solid var(--borde);border-radius:14px;padding:28px 28px;display:flex;flex-direction:column;gap:10px;counter-increment:p}
.pasos li::before{content:counter(p);width:44px;height:44px;border-radius:50%;background:var(--marino);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:20px;margin-bottom:6px}
.pasos b{font-size:23px;color:var(--marino2)}.pasos span{font-size:18px;color:var(--suave);line-height:1.45}
.documento{background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 30px 60px -20px rgba(15,39,64,.45),0 0 0 1px rgba(15,39,64,.1)}
.documento img{display:block;width:100%}
.portada-img .documento.flota{position:absolute;left:-30px;bottom:-20px;width:300px;transform:rotate(-4deg)}
.doc-grande{flex:0 0 520px;display:flex;justify-content:center}.doc-grande .documento{width:520px}
.doc-grande .documento img{max-height:640px;object-fit:cover;object-position:top}
.riesgo{display:flex;align-items:center;gap:36px;background:linear-gradient(135deg,#0f2740,#1a3a5c);color:#fff;border-radius:16px;padding:28px 40px}
.riesgo>span{font-size:54px;font-weight:300;color:#fca5a5;white-space:nowrap}
.riesgo b{font-size:24px;font-weight:600}.riesgo p{font-size:18.5px;color:#cbd5e1;line-height:1.45;margin-top:6px}
.fuentes{font-size:14.5px;color:var(--suave);line-height:1.5;margin-top:auto;margin-bottom:26px}
.flujo li.suyo{border:2px solid var(--azul);box-shadow:0 12px 30px -16px rgba(37,99,235,.6)}
.flujo li.suyo::before{background:var(--azul)}
.aviso-demo{position:absolute;right:72px;bottom:22px;font-size:13px;color:#94a3b8}
.portada .aviso-demo{color:#7f91a8}
#controles{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:8px;
 background:rgba(15,39,64,.82);backdrop-filter:blur(6px);padding:6px 10px;border-radius:999px;color:#fff;font-size:14px;z-index:5;transition:opacity .3s}
#controles button{background:none;border:0;color:#fff;font-size:18px;width:34px;height:34px;border-radius:50%;cursor:pointer}
#controles button:hover{background:rgba(255,255,255,.15)}
#contador{min-width:64px;text-align:center;font-variant-numeric:tabular-nums}
#progreso{position:fixed;left:0;top:0;height:3px;background:var(--azul);z-index:5;transition:width .3s}
body.quieto #controles{opacity:0}
@media print{
 @page{size:1600px 900px;margin:0}
 html,body{overflow:visible;background:#fff;height:auto}
 #escena{position:static;display:block}#lienzo{transform:none!important;width:1600px;height:auto}
 .diapo{position:relative;opacity:1;visibility:visible;width:1600px;height:900px;page-break-after:always;border-radius:0;
  -webkit-print-color-adjust:exact;print-color-adjust:exact}
 #controles,#progreso{display:none}
}
</style></head><body>
<div id="progreso"></div>
<div id="escena"><div id="lienzo">
__DIAPOS__
</div></div>
<nav id="controles" aria-label="Navegación"><button id="ant" title="Anterior (←)">‹</button><span id="contador"></span>
<button id="sig" title="Siguiente (→)">›</button><button id="pant" title="Pantalla completa (F)">⛶</button></nav>
<script>
(function(){
 const d=[...document.querySelectorAll('.diapo')],lienzo=document.getElementById('lienzo');
 d.forEach(s=>{const a=document.createElement('span');a.className='aviso-demo';a.textContent='Capturas de la demostración, con empresas y datos ficticios';
   if(s.querySelector('img'))s.appendChild(a);});
 let i=0;try{const h=parseInt(location.hash.slice(1));if(h>0&&h<=d.length)i=h-1;}catch(e){}
 function ver(n){i=Math.max(0,Math.min(d.length-1,n));d.forEach((s,k)=>s.classList.toggle('activa',k===i));
  document.getElementById('contador').textContent=(i+1)+' / '+d.length;
  document.getElementById('progreso').style.width=((i+1)/d.length*100)+'%';
  try{history.replaceState(null,'','#'+(i+1));}catch(e){}}
 function escala(){const k=Math.min(innerWidth/1600,innerHeight/900);lienzo.style.transform='scale('+k+')';}
 addEventListener('resize',escala);escala();ver(i);
 addEventListener('keydown',e=>{
  if(['ArrowRight','PageDown',' ','Enter'].includes(e.key)){e.preventDefault();ver(i+1);}
  else if(['ArrowLeft','PageUp','Backspace'].includes(e.key)){e.preventDefault();ver(i-1);}
  else if(e.key==='Home')ver(0);else if(e.key==='End')ver(d.length-1);
  else if(e.key==='f'||e.key==='F')pantalla();});
 document.getElementById('ant').onclick=()=>ver(i-1);document.getElementById('sig').onclick=()=>ver(i+1);
 function pantalla(){if(!document.fullscreenElement)document.documentElement.requestFullscreen().catch(()=>{});else document.exitFullscreen();}
 document.getElementById('pant').onclick=pantalla;
 let x0=null;addEventListener('touchstart',e=>{x0=e.touches[0].clientX},{passive:true});
 addEventListener('touchend',e=>{if(x0===null)return;const dx=e.changedTouches[0].clientX-x0;if(Math.abs(dx)>50)ver(i+(dx<0?1:-1));x0=null;});
 let t;function mov(){document.body.classList.remove('quieto');clearTimeout(t);t=setTimeout(()=>document.body.classList.add('quieto'),2500);}
 addEventListener('mousemove',mov);mov();
})();
</script>
</body></html>
"""


def main() -> None:
    html = PLANTILLA.replace("__DIAPOS__", construir())
    SALIDA.write_text(html, encoding="utf-8")
    print(f"{SALIDA} ({SALIDA.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
