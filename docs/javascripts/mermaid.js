document$.subscribe(function () {
  if (typeof mermaid === "undefined") {
    return;
  }

  document.querySelectorAll("pre.mermaid > code").forEach(function (code) {
    const pre = code.parentElement;
    const diagram = document.createElement("div");
    diagram.className = "mermaid";
    diagram.textContent = code.textContent;
    pre.replaceWith(diagram);
  });

  mermaid.initialize({
    startOnLoad: false,
    theme: document.body.getAttribute("data-md-color-scheme") === "slate" ? "dark" : "default",
    themeVariables: {
      fontSize: "18px"
    }
  });

  mermaid.run({
    querySelector: ".mermaid"
  });
});
