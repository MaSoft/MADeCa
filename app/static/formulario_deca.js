// Formulario del DeCA (revisión de borradores y alta manual).
// - Obligatorios del art. 6: el campo vacío se marca en rojo y su enlace en
//   el panel de comprobación se tacha con ✓ al rellenarlo.
// - Enlaces [data-ir]: llevan al campo, lo enfocan y lo resaltan.
// - Comprobación de cada campo con el servidor al terminar de escribirlo
//   (mismas reglas que al emitir) y aviso de referencia repetida.
// - Agenda de partes: sugerencias de DeCA anteriores al escribir nombre o NIF.
// Fichero propio porque la CSP del panel no admite JavaScript en línea.
(function () {
    "use strict";

    var form = document.querySelector("form[data-formulario-deca]");
    if (!form) { return; }
    var panel = document.querySelector("[data-comprobacion]");
    var enRevision = !!panel;

    function fila(nombre) { return document.getElementById("fila-" + nombre); }
    function campo(nombre) { return form.elements[nombre]; }
    function nota(nombre) { return document.getElementById("nota-" + nombre); }
    function vacio(nombre) { var c = campo(nombre); return !c || !String(c.value || "").trim(); }

    function falta(nombre) {
        var f = fila(nombre);
        if (!f || !f.hasAttribute("data-obligatorio")) { return false; }
        var alternativa = f.getAttribute("data-alternativa");
        return vacio(nombre) && (!alternativa || vacio(alternativa));
    }

    function ponerNota(nombre, texto) {
        var n = nota(nombre);
        if (n) { n.textContent = texto || ""; }
    }

    // ---------- Obligatorios y panel de comprobación ----------
    function actualizarObligatorio(nombre, forzar) {
        var f = fila(nombre);
        if (!f) { return; }
        var sinDato = falta(nombre);
        // En el alta manual solo se marca tras intentar enviar (forzar) o si ya estaba marcado.
        var marcar = sinDato && (enRevision || forzar || f.classList.contains("falta"));
        f.classList.toggle("falta", marcar);
        if (!f.classList.contains("con-error")) {
            if (marcar) {
                ponerNota(nombre, enRevision ? "Obligatorio: no aparece en el documento." : "Obligatorio.");
            } else {
                ponerNota(nombre, "");
            }
        }
        if (!panel) { return; }
        var li = panel.querySelector('li[data-para="' + nombre + '"]');
        if (li) {
            if (sinDato) {
                li.className = "falta";
            } else if (li.className === "falta" || li.className === "hecho") {
                li.className = "hecho";
            }
        }
    }

    function actualizarResumen() {
        if (!panel) { return; }
        var total = Number(panel.getAttribute("data-total"));
        var pendientes = panel.querySelectorAll("li.falta").length;
        var resumen = panel.querySelector("[data-resumen]");
        panel.classList.toggle("completa", pendientes === 0);
        resumen.textContent = pendientes
            ? "Faltan " + pendientes + " de " + total + " datos obligatorios"
            : "Están todos los datos obligatorios";
    }

    function obligatorios() {
        return Array.prototype.map.call(form.querySelectorAll("[data-obligatorio]"), function (f) {
            return f.getAttribute("data-campo");
        });
    }

    function revisarTodo(forzar) {
        obligatorios().forEach(function (n) { actualizarObligatorio(n, forzar); });
        actualizarResumen();
    }

    // ---------- Ir a un campo ----------
    function irA(nombre) {
        var c = campo(nombre);
        var f = fila(nombre);
        if (!c || !f) { return; }
        f.scrollIntoView({ behavior: "smooth", block: "center" });
        try { c.focus({ preventScroll: true }); } catch (e) { c.focus(); }
        f.classList.remove("resaltar");
        void f.offsetWidth;  // reinicia la animación
        f.classList.add("resaltar");
    }

    document.addEventListener("click", function (evento) {
        var enlace = evento.target.closest ? evento.target.closest("[data-ir]") : null;
        if (!enlace) { return; }
        evento.preventDefault();
        irA(enlace.getAttribute("data-ir"));
    });

    // ---------- Comprobación con el servidor ----------
    var peticiones = {};

    function comprobar(nombre) {
        var c = campo(nombre);
        var f = fila(nombre);
        if (!c || !f || !c.hasAttribute("data-validar")) { return; }
        var datos = new FormData();
        datos.append("campo", nombre);
        datos.append("valor", c.value || "");
        var pais = { matricula_vehiculo: "pais_matricula_vehiculo", matricula_remolque: "pais_matricula_remolque" }[nombre];
        if (pais && campo(pais)) { datos.append("pais", (campo(pais).value || "ES").toUpperCase()); }
        var numero = (peticiones[nombre] || 0) + 1;
        peticiones[nombre] = numero;
        fetch("/dashboard/formulario/validar", { method: "POST", body: datos, credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (r) {
                if (!r || peticiones[nombre] !== numero) { return; }  // respuesta vieja
                f.classList.toggle("con-error", !r.ok);
                f.classList.toggle("valido", r.ok && !!String(c.value || "").trim());
                quitarAvisoRepetido(f);
                if (!r.ok) {
                    ponerNota(nombre, r.mensaje);
                } else {
                    actualizarObligatorio(nombre);
                    if (!f.classList.contains("falta")) { ponerNota(nombre, ""); }
                }
                if (r.aviso) { mostrarAvisoRepetido(f, r.aviso); }
                var errorPanel = panel && panel.querySelector('[data-error-para="' + nombre + '"]');
                if (errorPanel) { errorPanel.classList.toggle("hecho", r.ok); }
            })
            .catch(function () { /* sin red: ya lo comprobará el servidor al emitir */ });
    }

    function quitarAvisoRepetido(f) {
        var previo = f.querySelector(".aviso-campo");
        if (previo) { previo.parentNode.removeChild(previo); }
    }

    function mostrarAvisoRepetido(f, aviso) {
        var p = document.createElement("p");
        p.className = "aviso-campo";
        p.appendChild(document.createTextNode(aviso.texto + " "));
        var a = document.createElement("a");
        a.href = aviso.enlace;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = "Ver expedición";
        p.appendChild(a);
        f.appendChild(p);
    }

    form.addEventListener("input", function (evento) {
        var nombre = evento.target.name;
        var f = fila(nombre);
        if (!f) { return; }
        if (f.classList.contains("con-error")) {
            f.classList.remove("con-error");
            ponerNota(nombre, "");
        }
        f.classList.remove("valido");
        // El campo que cubre a otro (otra magnitud -> peso) también cuenta.
        obligatorios().forEach(function (n) {
            var o = fila(n);
            if (n === nombre || (o && o.getAttribute("data-alternativa") === nombre)) { actualizarObligatorio(n); }
        });
        actualizarResumen();
    });

    form.addEventListener("change", function (evento) {
        var nombre = evento.target.name;
        if (nombre === "pais_matricula_vehiculo") { comprobar("matricula_vehiculo"); }
        if (nombre === "pais_matricula_remolque") { comprobar("matricula_remolque"); }
        comprobar(nombre);
    });

    form.addEventListener("submit", function (evento) {
        revisarTodo(true);
        var primero = form.querySelector(".campo.falta, .campo.con-error");
        if (primero) {
            evento.preventDefault();
            if (panel) { panel.scrollIntoView({ behavior: "smooth", block: "start" }); }
            irA(primero.getAttribute("data-campo"));
        }
    });

    // ---------- Agenda de partes ----------
    var lista = null;
    var listaPara = null;
    var activa = -1;
    var espera = null;

    function cerrarLista() {
        if (lista && lista.parentNode) { lista.parentNode.removeChild(lista); }
        lista = null;
        listaPara = null;
        activa = -1;
    }

    function elegir(parte, p) {
        var valores = { nombre: p.nombre, nif: p.nif, domicilio: p.domicilio };
        Object.keys(valores).forEach(function (papel) {
            var c = form.querySelector('[data-parte="' + parte + '"][data-papel="' + papel + '"]');
            if (!c || (papel === "domicilio" && !valores[papel])) { return; }
            c.value = valores[papel];
            c.dispatchEvent(new Event("input", { bubbles: true }));
            c.dispatchEvent(new Event("change", { bubbles: true }));
        });
        cerrarLista();
    }

    function pintarLista(entrada, partes) {
        cerrarLista();
        if (!partes.length) { return; }
        var parte = entrada.getAttribute("data-parte");
        lista = document.createElement("ul");
        lista.className = "sugerencias";
        lista.setAttribute("role", "listbox");
        partes.forEach(function (p, i) {
            var li = document.createElement("li");
            li.setAttribute("role", "option");
            var nombre = document.createElement("strong");
            nombre.textContent = p.nombre;
            var detalle = document.createElement("span");
            detalle.textContent = p.nif + (p.domicilio ? " · " + p.domicilio : "") + " · " + p.usos + (p.usos === 1 ? " DeCA" : " DeCA");
            li.appendChild(nombre);
            li.appendChild(detalle);
            li.addEventListener("mousedown", function (e) { e.preventDefault(); elegir(parte, p); });
            li.addEventListener("mouseenter", function () { marcarActiva(i); });
            lista.appendChild(li);
        });
        var titulo = document.createElement("li");
        titulo.className = "sugerencias-titulo";
        titulo.textContent = "De DeCA anteriores";
        lista.insertBefore(titulo, lista.firstChild);
        entrada.parentNode.appendChild(lista);
        listaPara = { entrada: entrada, partes: partes, parte: parte };
    }

    function marcarActiva(i) {
        if (!lista) { return; }
        var opciones = lista.querySelectorAll('[role="option"]');
        activa = Math.max(-1, Math.min(i, opciones.length - 1));
        Array.prototype.forEach.call(opciones, function (o, j) { o.classList.toggle("activa", j === activa); });
    }

    form.addEventListener("input", function (evento) {
        var entrada = evento.target;
        var papel = entrada.getAttribute && entrada.getAttribute("data-papel");
        if (!evento.isTrusted || (papel !== "nombre" && papel !== "nif")) { return; }
        clearTimeout(espera);
        var texto = (entrada.value || "").trim();
        if (texto.length < 2) { cerrarLista(); return; }
        espera = setTimeout(function () {
            var datos = new FormData();
            datos.append("texto", texto);
            fetch("/dashboard/formulario/partes", { method: "POST", body: datos, credentials: "same-origin" })
                .then(function (r) { return r.ok ? r.json() : { partes: [] }; })
                .then(function (r) {
                    if (document.activeElement === entrada && (entrada.value || "").trim() === texto) { pintarLista(entrada, r.partes || []); }
                })
                .catch(function () {});
        }, 250);
    });

    form.addEventListener("keydown", function (evento) {
        if (!lista || !listaPara || evento.target !== listaPara.entrada) { return; }
        if (evento.key === "ArrowDown") { evento.preventDefault(); marcarActiva(activa + 1); }
        else if (evento.key === "ArrowUp") { evento.preventDefault(); marcarActiva(activa - 1); }
        else if (evento.key === "Enter" && activa >= 0) { evento.preventDefault(); elegir(listaPara.parte, listaPara.partes[activa]); }
        else if (evento.key === "Escape") { cerrarLista(); }
    });

    form.addEventListener("focusout", function (evento) {
        if (listaPara && evento.target === listaPara.entrada) { setTimeout(cerrarLista, 150); }
    });

    // ---------- Dónde se leyó cada dato (documento original) ----------
    var documento = document.querySelector("[data-doc-paginas]");

    function senalar(nombre) {
        if (!documento) { return; }
        documento.querySelectorAll(".marca-dato.activa").forEach(function (m) { m.classList.remove("activa"); });
        var marca = documento.querySelector('.marca-dato[data-marca="' + nombre + '"]');
        if (!marca) { return; }
        marca.classList.add("activa");
        // Se desplaza el visor (la tarjeta del documento) hasta el dato.
        var visor = documento.closest(".revision-documento") || documento;
        var desplazable = visor.scrollHeight > visor.clientHeight ? visor : null;
        if (desplazable) {
            var destino = marca.getBoundingClientRect().top - desplazable.getBoundingClientRect().top + desplazable.scrollTop
                - desplazable.clientHeight / 3;
            desplazable.scrollTo({ top: Math.max(0, destino), behavior: "smooth" });
        } else {
            var r = marca.getBoundingClientRect();
            if (r.top < 0 || r.bottom > window.innerHeight) { marca.scrollIntoView({ behavior: "smooth", block: "center" }); }
        }
    }

    form.addEventListener("focusin", function (evento) {
        if (evento.target.name) { senalar(evento.target.name); }
    });

    if (documento) {
        documento.addEventListener("click", function (evento) {
            var marca = evento.target.closest(".marca-dato");
            if (marca) { irA(marca.getAttribute("data-marca")); }
        });
        var verTodas = document.querySelector("[data-ver-marcas]");
        if (verTodas) {
            verTodas.addEventListener("change", function () { documento.classList.toggle("ver-todas", verTodas.checked); });
        }
    }

    revisarTodo(false);
})();
