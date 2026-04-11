{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = [
    (pkgs.python312.withPackages (ps: with ps; [
      httpx
      beautifulsoup4
      lxml
      pytest
      pytest-asyncio
      respx
      mypy
    ]))
  ];
}
