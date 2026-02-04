#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path

LEADERS_PATH = Path("lideres.json")
EMPLOYEES_PATH = Path("funcionarios-ativos.json")


def load_json(path):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_employee(employees, cpf=None, employee_id=None):
    for employee in employees:
        if employee_id and str(employee.get("id")) == str(employee_id):
            return employee
        if cpf and employee.get("cpf") and "".join(filter(str.isdigit, employee.get("cpf"))) == "".join(filter(str.isdigit, cpf)):
            return employee
    return None


def build_leader_from_employee(employee, sectors, email=None, phone=None, observations=None):
    now = datetime.utcnow().isoformat()
    cpf = employee.get("cpf") or ""
    name = employee.get("nome_completo") or employee.get("nome") or "Líder (sem nome)"
    return {
        "id": employee.get("id") or employee.get("cpf") or f"lead-{int(datetime.utcnow().timestamp())}",
        "nome": name,
        "orgao": employee.get("empresa") or "Popular Atacarejo",
        "email": email or employee.get("email"),
        "telefone": phone or employee.get("telefone"),
        "cpf": cpf,
        "setores_responsaveis": sectors,
        "observacoes": observations or f"Promovido em {now}",
        "salvo_em": now,
        "atualizado_em": now,
        "lider_gestor": False,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Promove um funcionário ao quadro de líderes lendo os JSONs existentes."
    )
    parser.add_argument("--cpf", help="CPF do funcionário a promover (aceita apenas dígitos).")
    parser.add_argument("--employee-id", help="ID interno do funcionário, se presente.")
    parser.add_argument("--sectors", nargs="+", help="Lista de setores atribuídos ao líder.", default=[])
    parser.add_argument("--email", help="Email do líder caso deseje sobrescrever.")
    parser.add_argument("--phone", help="Telefone do líder.")
    parser.add_argument("--observations", help="Observações adicionais.")
    parser.add_argument("--force", action="store_true", help="Substitui líder existente com mesmo CPF.")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.cpf and not args.employee_id:
        raise SystemExit("Informe --cpf ou --employee-id para localizar o funcionário.")

    leaders = load_json(LEADERS_PATH)
    employees = load_json(EMPLOYEES_PATH)

    employee = find_employee(employees, cpf=args.cpf, employee_id=args.employee_id)
    if not employee:
        raise SystemExit("Funcionário não encontrado nos registros ativos.")

    existing = next(
        (leader for leader in leaders if leader.get("cpf") and employee.get("cpf") and leader.get("cpf").replace(".", "").replace("-", "") == employee.get("cpf").replace(".", "").replace("-", "")),
        None,
    )
    if existing and not args.force:
        raise SystemExit("Já existe um líder com este CPF. Use --force para substituir.")

    leader_entry = build_leader_from_employee(employee, args.sectors, email=args.email, phone=args.phone, observations=args.observations)
    if existing:
        leaders = [leader for leader in leaders if leader.get("cpf") != existing.get("cpf")]
        print("Substituindo líder existente.")

    leaders.append(leader_entry)
    dump_json(LEADERS_PATH, leaders)
    print(f"Líder '{leader_entry['nome']}' registrado com sucesso.")


if __name__ == "__main__":
    main()
