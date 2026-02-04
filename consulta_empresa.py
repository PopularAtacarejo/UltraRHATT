# coding: utf-8
import requests
from typing import Any, Dict, List

API_BASE_URL = "https://open.cnpja.com/office"


def limpar_cnpj(cnpj: str) -> str:
    return "".join(filter(str.isdigit, cnpj))


def consultar_cnpj(cnpj: str) -> Dict[str, Any]:
    cnpj_limpo = limpar_cnpj(cnpj)
    if len(cnpj_limpo) != 14:
        raise ValueError("CNPJ inválido!")

    url = f"{API_BASE_URL}/{cnpj_limpo}"
    response = requests.get(url, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Erro na consulta: {response.status_code} - {response.text}")

    return response.json()


def formatar_membros(members: List[Dict[str, Any]]) -> List[str]:
    if not members:
        return ["Nenhum sócio informado."]
    filas: List[str] = []
    for socio in members:
        pessoa = socio.get("person", {})
        role = socio.get("role", {}).get("text", "—")
        since = socio.get("since")
        idade = pessoa.get("age", "—")
        tax_id = pessoa.get("taxId") or pessoa.get("id", "—")
        nome = pessoa.get("name", "Sócio")
        trecho = f"- {nome} ({role}) - {tax_id} - Idade: {idade}"
        if since:
            trecho += f" desde {since}"
        filas.append(trecho)
    return filas


def formatar_cnaes(atividades: List[Dict[str, Any]]) -> List[str]:
    if not atividades:
        return ["Não possui atividades secundárias."]
    return [f"- {item.get('id', '—')} - {item.get('text', '—')}" for item in atividades]


def formatar_contatos(telefones: List[Dict[str, Any]], emails: List[Dict[str, Any]]) -> List[str]:
    linhas = []
    for telefone in telefones:
        ddd = telefone.get("ddd")
        numero = telefone.get("number", "—")
        tipo = telefone.get("type")
        prefixo = f"({ddd}) " if ddd else ""
        sufixo = f" [{tipo}]" if tipo else ""
        linhas.append(f"- {prefixo}{numero}{sufixo}".strip())
    for email in emails:
        linhas.append(f"- Email: {email.get('address', '—')} (Domínio: {email.get('domain', '—')})")
    return linhas or ["Nenhum contato registrado."]


def formatar_endereco(endereco: Dict[str, Any]) -> str:
    parts = [
        endereco.get("street"),
        endereco.get("number"),
        endereco.get("district"),
        endereco.get("city"),
        endereco.get("state"),
        endereco.get("zip"),
    ]
    valores = [part for part in parts if part]
    return " • ".join(valores) if valores else "—"


def formatar_dados(data: Dict[str, Any]):
    print("\n================= DADOS DO CNPJ =================")
    print(f"CNPJ..................: {data.get('taxId')}")
    print(f"Nome Fantasia.........: {data.get('alias')}")
    print(f"Razão Social..........: {data.get('company', {}).get('name')}")
    print(f"Data de Abertura......: {data.get('founded')}")
    print(f"Situação..............: {data.get('status', {}).get('text', '-')}")
    print(f"Data da Situação......: {data.get('statusDate')}")
    print(f"Empresa Matriz?.......: {'Sim' if data.get('head') else 'Filial'}")

    company = data.get("company", {})
    print("\n----- EMPRESA -----")
    print(f"Natureza Jurídica.....: {company.get('nature', {}).get('text', '—')}")
    print(f"Capital Social........: {company.get('equity', '—')}")
    print(f"Porte.................: {company.get('size', {}).get('text', '—')}")
    print(f"Optante do Simples....: {company.get('simples', {}).get('optant', '—')}")
    print(f"Optante do MEI........: {company.get('simei', {}).get('optant', '—')}")

    print("\n----- SÓCIOS -----")
    for linha in formatar_membros(company.get("members", [])):
        print(linha)

    endereco = data.get("address", {})
    print("\n----- ENDEREÇO -----")
    print(formatar_endereco(endereco))

    print("\n----- CONTATO -----")
    for contato in formatar_contatos(data.get("phones", []), data.get("emails", [])):
        print(contato)

    main = data.get("mainActivity", {})
    print("\n----- CNAE PRINCIPAL -----")
    print(f"{main.get('id', '—')} - {main.get('text', '—')}")

    print("\n----- CNAEs SECUNDÁRIOS -----")
    for cnae in formatar_cnaes(data.get("sideActivities", [])):
        print(cnae)

    print("\n=================================================\n")


def main():
    print("=== Consulta de CNPJ ===")
    while True:
        cnpj = input("Digite o CNPJ (ou ENTER para sair): ").strip()
        if not cnpj:
            print("Saindo...")
            break

        try:
            dados = consultar_cnpj(cnpj)
            formatar_dados(dados)
        except Exception as exc:
            print(f"\n[ERRO] {exc}\n")


if __name__ == "__main__":
    main()
