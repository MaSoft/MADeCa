// Bandeja de revisión: selección de varios pendientes y acciones en lote
// (emitir los completos / descartar) con confirmación en la propia página.
// Por delegación: la tabla se sustituye al filtrar.
(function () {
    "use strict";

    function barra() { return document.querySelector("[data-lote]"); }
    function casillas() { return Array.prototype.slice.call(document.querySelectorAll('input[name="ids"][form="form-lote"]')); }

    function paso(nombre) {
        var b = barra();
        if (!b) { return; }
        b.querySelectorAll("[data-paso]").forEach(function (p) { p.hidden = p.getAttribute("data-paso") !== nombre; });
        var confirmo = b.querySelector("[data-lote-confirmo]");
        if (confirmo && nombre !== "emitir") { confirmo.checked = false; }
        var emitir = b.querySelector("[data-lote-emitir]");
        if (emitir) { emitir.disabled = !(confirmo && confirmo.checked); }
    }

    function actualizar() {
        var b = barra();
        if (!b) { return; }
        var todas = casillas();
        var marcadas = todas.filter(function (c) { return c.checked; }).length;
        b.hidden = marcadas === 0;
        b.querySelector("[data-n-seleccion]").textContent = marcadas;
        b.querySelector("[data-texto-seleccion]").textContent = marcadas === 1 ? "seleccionado" : "seleccionados";
        var todos = document.querySelector("[data-lote-todos]");
        if (todos) {
            todos.checked = marcadas > 0 && marcadas === todas.length;
            todos.indeterminate = marcadas > 0 && marcadas < todas.length;
        }
        casillas().forEach(function (c) { var fila = c.closest("tr"); if (fila) { fila.classList.toggle("seleccionada", c.checked); } });
        if (marcadas === 0) { paso("inicio"); }
    }

    document.addEventListener("change", function (e) {
        if (e.target.matches("[data-lote-todos]")) {
            casillas().forEach(function (c) { c.checked = e.target.checked; });
            actualizar();
        } else if (e.target.matches('input[name="ids"][form="form-lote"]')) {
            actualizar();
        } else if (e.target.matches("[data-lote-confirmo]")) {
            barra().querySelector("[data-lote-emitir]").disabled = !e.target.checked;
        }
    });

    document.addEventListener("click", function (e) {
        var t = e.target.closest ? e.target.closest("[data-lote-pedir],[data-lote-cancelar],[data-lote-limpiar]") : null;
        if (!t) { return; }
        if (t.hasAttribute("data-lote-pedir")) { paso(t.getAttribute("data-lote-pedir")); }
        else if (t.hasAttribute("data-lote-cancelar")) { paso("inicio"); }
        else { casillas().forEach(function (c) { c.checked = false; }); actualizar(); }
    });
})();
