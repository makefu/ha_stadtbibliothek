{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
        ] (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: {
        default = pkgs.stdenvNoCC.mkDerivation {
          pname = "ha-stadtbibliothek";
          version = "0.1.0";
          src = ./.;
          installPhase = ''
            mkdir -p $out/custom_components
            cp -r custom_components/stadtbibliothek $out/custom_components/stadtbibliothek
          '';
        };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python313.withPackages (ps: [
              ps.pytest
              ps.pytest-asyncio
              ps.httpx
              ps.beautifulsoup4
              ps.html5lib
              ps.respx
              ps.ruff
              ps.mypy
              ps.voluptuous
              ps.freezegun
            ]))
          ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-rfc-style);
    };
}
