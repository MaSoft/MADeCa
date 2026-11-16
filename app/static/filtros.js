// Barras de filtros del panel (Expediciones y Bandeja de revisión).
// Fichero propio porque la CSP del panel no admite JavaScript en línea.
// El formulario [data-filtros] es la única fuente de los filtros: las
// fichas «✕» y las cabeceras ordenables cambian sus campos y lo envían
// (htmx escucha su evento «change»).
(function () {
    "use strict";

    function formulario() {
        return document.querySelector("form[data-filtros]");
    }

    function enviar(form) {
        form.dispatchEvent(new Event("change", { bubbles: true }));
    }

    // Marca la opción con ese valor. Asignar RadioNodeList.value = "" no
    // marca la opción de valor vacío en Chromium: se recorre a mano.
    function marcar(grupo, valor) {
        Array.prototype.forEach.call(grupo, function (r) { r.checked = (r.value === valor); });
    }

    function vaciar(form, nombre) {
        var campo = form.elements[nombre];
        if (!campo) { return; }
        if (campo instanceof RadioNodeList) {
            marcar(campo, "");
        } else if (campo.type === "checkbox") {
            campo.checked = false;
        } else {
            campo.value = "";
        }
    }

    // Periodo y «desde/hasta» se excluyen: elegir un periodo vacía las
    // fechas y escribir una fecha pasa el periodo a «Entre fechas». En
    // captura, para que el cambio esté hecho cuando htmx envía.
    document.addEventListener("change", function (evento) {
        var campo = evento.target;
        var form = campo.form;
        if (!form || !form.hasAttribute("data-filtros")) { return; }
        // Cualquier filtro nuevo vuelve a la primera página.
        if (form.elements.pagina && campo.name !== "pagina") { form.elements.pagina.value = "1"; }
        if (campo.name === "periodo" && campo.value !== "rango") {
            vaciar(form, "desde");
            vaciar(form, "hasta");
        } else if ((campo.name === "desde" || campo.name === "hasta") && form.elements.periodo) {
            marcar(form.elements.periodo, (form.elements.desde.value || form.elements.hasta.value) ? "rango" : "");
        }
    }, true);

    // Al escribir en un buscador (evento «input», no «change») también se
    // vuelve a la primera página.
    document.addEventListener("input", function (evento) {
        var form = evento.target.form;
        if (form && form.hasAttribute("data-filtros") && form.elements.pagina && evento.target.type === "search") {
            form.elements.pagina.value = "1";
        }
    }, true);

    document.addEventListener("click", function (evento) {
        var form = formulario();
        var objetivo = evento.target.closest ? evento.target : null;
        if (!objetivo) { return; }

        // Ficha de filtro activo: vacía sus campos.
        // Paginador: cambia de página conservando filtros y orden.
        var botonPagina = objetivo.closest("[data-pagina]");
        if (botonPagina && form && form.elements.pagina && !botonPagina.disabled) {
            form.elements.pagina.value = botonPagina.getAttribute("data-pagina");
            enviar(form);
            var resultados = document.getElementById("resultados");
            if (resultados) { resultados.scrollIntoView({ behavior: "smooth", block: "start" }); }
            return;
        }

        var ficha = objetivo.closest("[data-quitar]");
        if (ficha && form) {
            if (form.elements.pagina) { form.elements.pagina.value = "1"; }
            ficha.getAttribute("data-quitar").split(" ").forEach(function (nombre) { vaciar(form, nombre); });
            enviar(form);
            return;
        }

        // Cabecera ordenable.
        var cabecera = objetivo.closest("[data-orden]");
        if (cabecera && form && form.elements.orden) {
            form.elements.orden.value = cabecera.getAttribute("data-orden");
            if (form.elements.pagina) { form.elements.pagina.value = "1"; }
            enviar(form);
            return;
        }

        // Al abrir un desplegable se cierran los demás; un clic fuera los cierra.
        var abierto = objetivo.closest("details[data-desplegable]");
        document.querySelectorAll("details[data-desplegable][open]").forEach(function (d) {
            if (d !== abierto) { d.removeAttribute("open"); }
        });
    });

    document.addEventListener("keydown", function (evento) {
        if (evento.key !== "Escape") { return; }
        document.querySelectorAll("details[data-desplegable][open]").forEach(function (d) {
            d.removeAttribute("open");
            var resumen = d.querySelector("summary");
            if (resumen) { resumen.focus(); }
        });
    });
})();
