{
  lib,
  buildPythonPackage,
  setuptools,
  httpx,
  beautifulsoup4,
  html5lib,
  lxml,
}:

buildPythonPackage rec {
  pname = "ha-stadtbibliothek";
  version =
    (builtins.fromJSON (builtins.readFile ../custom_components/stadtbibliothek/manifest.json)).version;

  src = lib.cleanSource ../.;
  pyproject = true;

  build-system = [ setuptools ];

  dependencies = [
    httpx
    beautifulsoup4
    html5lib
    lxml
  ];

  pythonImportsCheck = [
    "custom_components.stadtbibliothek.backends"
    "custom_components.stadtbibliothek.serializers"
  ];

  passthru = {
    isHomeAssistantComponent = true;
    domain = "stadtbibliothek";
  };

  meta = {
    description = "Stadtbibliothek library account backends, CLI tools and Home Assistant integration";
    homepage = "https://github.com/makefu/ha_stadtbibliothek";
    license = lib.licenses.mit;
    mainProgram = "stadtbibliothek-remseck";
  };
}
