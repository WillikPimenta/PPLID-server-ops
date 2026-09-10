from datetime import date

from django.core.management.base import BaseCommand

from apps.communication.models import News

SAMPLE_NEWS = [
    {
        "category": "Qualidade",
        "published_at": date(2026, 5, 28),
        "author": "Equipe de Qualidade",
        "title": "Resultados da auditoria interna do 2º trimestre",
        "summary": "A auditoria interna do segundo trimestre foi concluída com resultados positivos e poucas não conformidades. Veja os principais destaques e os próximos passos.",
        "content": "A auditoria interna referente ao segundo trimestre de 2026 foi concluída na última semana, abrangendo todos os processos críticos da operação. O resultado geral foi considerado positivo pela equipe de Qualidade.\nForam identificadas apenas três não conformidades de baixo impacto, todas relacionadas a registros de documentação. Os planos de ação corretiva já foram abertos e têm prazo de 30 dias para conclusão.\nAgradecemos o empenho de todas as áreas envolvidas. A próxima auditoria está prevista para o início do terceiro trimestre, e o cronograma detalhado será divulgado em breve no portal da Qualidade.",
    },
    {
        "category": "Operação",
        "published_at": date(2026, 5, 27),
        "author": "Coordenação de Operação",
        "title": "Novo procedimento de abertura de turno entra em vigor",
        "summary": "A partir de 1º de junho, o procedimento de abertura de turno terá um checklist digital. Entenda o que muda no dia a dia das equipes.",
        "content": "A partir do dia 1º de junho, todas as equipes deverão utilizar o novo checklist digital de abertura de turno, disponível diretamente no sistema interno.\nO objetivo da mudança é reduzir falhas de comunicação entre turnos e garantir que todos os pontos críticos sejam verificados antes do início das atividades.\nTreinamentos rápidos de 15 minutos serão realizados ao longo da próxima semana. A presença dos líderes de turno é obrigatória.",
    },
    {
        "category": "Planejamento",
        "published_at": date(2026, 5, 26),
        "author": "Equipe de Planejamento",
        "title": "Forecast de demanda para junho já está disponível",
        "summary": "As previsões de volume para o mês de junho foram publicadas. O período aponta para um crescimento moderado da demanda.",
        "content": "A equipe de Planejamento divulgou o forecast de demanda para o mês de junho. As projeções indicam um crescimento moderado de aproximadamente 8% em relação a maio.\nCom base nesses números, o dimensionamento das equipes já foi ajustado e as escalas serão revisadas para garantir o atendimento dentro das metas de nível de serviço.\nOs líderes podem consultar os detalhes completos do forecast na seção de Planejamento da intranet.",
    },
    {
        "category": "Processos",
        "published_at": date(2026, 5, 23),
        "author": "Time de Processos",
        "title": "Mapeamento de processos do setor de cadastro é revisado",
        "summary": "O fluxo do setor de cadastro passou por uma revisão completa, eliminando etapas redundantes e reduzindo o tempo de execução.",
        "content": "O time de Processos concluiu a revisão do mapeamento do setor de cadastro. A análise identificou três etapas redundantes que foram eliminadas do fluxo.\nCom as mudanças, o tempo médio de execução do processo caiu de 12 para 8 minutos, um ganho de produtividade de cerca de 33%.\nO novo fluxograma e os POPs atualizados já estão disponíveis na seção de Processos. Recomendamos que todos os colaboradores envolvidos façam a leitura.",
    },
    {
        "category": "Indicadores",
        "published_at": date(2026, 5, 22),
        "author": "BI & Indicadores",
        "title": "Dashboard de headcount ganha novos filtros",
        "summary": "O painel de headcount foi atualizado com filtros por área, turno e tipo de contrato, facilitando análises mais detalhadas.",
        "content": "O dashboard de headcount recebeu uma atualização importante: agora é possível filtrar os dados por área, turno e tipo de contrato.\nEssas novas opções permitem análises mais granulares e apoiam a tomada de decisão sobre contratações e remanejamentos.\nAcesse o painel pela seção de Indicadores ou pelo atalho nos Links Úteis para explorar as novidades.",
    },
    {
        "category": "RH",
        "published_at": date(2026, 5, 20),
        "author": "Recursos Humanos",
        "title": "Nova política de home office é divulgada",
        "summary": "O RH publicou as diretrizes atualizadas para trabalho remoto, com regras claras sobre dias presenciais e elegibilidade.",
        "content": "O departamento de Recursos Humanos divulgou a nova política de home office, válida a partir do próximo mês.\nA política estabelece um modelo híbrido, com a definição de dias presenciais por área e critérios de elegibilidade para o trabalho remoto.\nO documento completo está disponível na seção de Políticas Internas. Dúvidas podem ser encaminhadas ao canal de atendimento do RH.",
    },
    {
        "category": "Geral",
        "published_at": date(2026, 5, 19),
        "author": "Comunicação Interna",
        "title": "Campanha de vacinação com agendamento aberto",
        "summary": "A campanha anual de vacinação contra a gripe está com agendamentos abertos. Garanta seu horário pelo portal do RH.",
        "content": "Teve início a campanha anual de vacinação contra a gripe, oferecida gratuitamente a todos os colaboradores.\nOs agendamentos já estão disponíveis no portal do RH e devem ser feitos até o dia 10 de junho. A aplicação acontecerá no ambulatório da empresa.\nCuide da sua saúde e da saúde dos colegas. A adesão é voluntária, mas fortemente recomendada.",
    },
    {
        "category": "Operação",
        "published_at": date(2026, 5, 16),
        "author": "Coordenação de Operação",
        "title": "Reconhecimento das equipes de melhor desempenho de abril",
        "summary": "As equipes que se destacaram nos indicadores de abril foram reconhecidas. Confira os times premiados e os critérios avaliados.",
        "content": "Reconhecemos as equipes que apresentaram os melhores desempenhos nos indicadores operacionais do mês de abril.\nOs critérios avaliados incluíram produtividade, qualidade do atendimento e aderência aos procedimentos. Parabéns aos times premiados!\nO reconhecimento faz parte do programa de valorização contínua das equipes. Os próximos resultados serão divulgados mensalmente.",
    },
    {
        "category": "Planejamento",
        "published_at": date(2026, 5, 14),
        "author": "Equipe de Planejamento",
        "title": "Revisão das escalas de junho começa na próxima semana",
        "summary": "O processo de revisão das escalas de junho terá início na próxima semana. Líderes devem enviar suas demandas até sexta-feira.",
        "content": "Na próxima semana iniciaremos o processo de revisão das escalas referentes ao mês de junho.\nSolicitamos que todos os líderes encaminhem suas demandas e particularidades de equipe até sexta-feira, para que possamos consolidar o planejamento.\nAs escalas finais serão publicadas com antecedência mínima de 15 dias, conforme acordo coletivo.",
    },
    {
        "category": "Qualidade",
        "published_at": date(2026, 5, 12),
        "author": "Equipe de Qualidade",
        "title": "Treinamento sobre tratamento de não conformidades",
        "summary": "Está aberta a inscrição para o treinamento sobre registro e tratamento de não conformidades. Vagas limitadas.",
        "content": "A equipe de Qualidade promoverá um treinamento sobre o registro e o tratamento correto de não conformidades.\nO conteúdo abordará desde a identificação do problema até a definição e o acompanhamento de ações corretivas e preventivas.\nAs inscrições estão abertas e as vagas são limitadas. Reserve a sua pela seção de Qualidade na intranet.",
    },
    {
        "category": "Indicadores",
        "published_at": date(2026, 5, 9),
        "author": "BI & Indicadores",
        "title": "Relatório mensal de produtividade já disponível",
        "summary": "O relatório de produtividade de abril foi publicado, com comparativos entre áreas e evolução histórica dos indicadores.",
        "content": "O relatório mensal de produtividade referente a abril já está disponível para consulta na seção de Indicadores.\nO documento traz comparativos entre as áreas e a evolução histórica dos principais indicadores operacionais.\nUse esses dados para apoiar as reuniões de resultado e os planos de ação de cada equipe.",
    },
    {
        "category": "Operação",
        "published_at": date(2026, 5, 8),
        "author": "Coordenação de Operação",
        "title": "Manutenção programada de sistemas no fim de semana",
        "summary": "Haverá manutenção programada nos sistemas internos no próximo sábado. Planeje suas atividades com antecedência.",
        "content": "Informamos que haverá manutenção programada nos sistemas internos no próximo sábado, das 8h às 12h.\nDurante esse período, alguns serviços poderão ficar indisponíveis. Recomendamos planejar as atividades com antecedência.\nA equipe de TI estará de plantão para garantir o retorno dos sistemas dentro do prazo previsto.",
    },
    {
        "category": "RH",
        "published_at": date(2026, 5, 6),
        "author": "Recursos Humanos",
        "title": "Programa de indicação de talentos é relançado",
        "summary": "O programa de indicação de novos talentos está de volta, com bonificação para colaboradores que indicarem contratações.",
        "content": "O RH relançou o programa de indicação de talentos, incentivando os colaboradores a indicarem profissionais para as vagas abertas.\nCada indicação que resultar em contratação efetivada gera uma bonificação ao colaborador indicante.\nConfira as vagas disponíveis e o regulamento completo no portal do RH.",
    },
    {
        "category": "Processos",
        "published_at": date(2026, 5, 5),
        "author": "Time de Processos",
        "title": "Padronização de nomenclatura de documentos",
        "summary": "Uma nova padronização de nomenclatura de documentos foi definida para facilitar buscas e organização de arquivos.",
        "content": "O time de Processos definiu uma nova padronização para a nomenclatura de documentos internos.\nO objetivo é facilitar buscas, evitar duplicidades e melhorar a organização dos arquivos compartilhados.\nO guia de nomenclatura está disponível na seção de Processos e deve ser adotado a partir desta semana.",
    },
    {
        "category": "Geral",
        "published_at": date(2026, 5, 2),
        "author": "Comunicação Interna",
        "title": "Resultados da pesquisa de clima organizacional",
        "summary": "Os resultados da pesquisa de clima foram consolidados. Confira os destaques e os planos de ação para os próximos meses.",
        "content": "Os resultados da pesquisa de clima organizacional foram consolidados e apresentam um índice geral de satisfação de 82%.\nEntre os destaques positivos estão o relacionamento com a liderança e o ambiente de trabalho. Pontos de melhoria também foram mapeados.\nPlanos de ação serão construídos junto às áreas ao longo dos próximos meses.",
    },
    {
        "category": "Qualidade",
        "published_at": date(2026, 4, 30),
        "author": "Equipe de Qualidade",
        "title": "Certificação ISO 9001 é renovada com sucesso",
        "summary": "A empresa renovou a certificação ISO 9001 após auditoria externa. Um marco importante para a gestão da qualidade.",
        "content": "Temos o prazer de anunciar que a certificação ISO 9001 foi renovada com sucesso após a auditoria externa realizada neste mês.\nO resultado reflete o comprometimento de todas as áreas com a melhoria contínua e a padronização dos processos.\nAgradecemos a colaboração de todos. Seguiremos trabalhando para manter e elevar o nível de excelência.",
    },
    {
        "category": "Planejamento",
        "published_at": date(2026, 4, 28),
        "author": "Equipe de Planejamento",
        "title": "Novo modelo de dimensionamento por sazonalidade",
        "summary": "Foi adotado um novo modelo de dimensionamento que considera a sazonalidade da demanda ao longo do ano.",
        "content": "A equipe de Planejamento implementou um novo modelo de dimensionamento que leva em conta a sazonalidade da demanda.\nCom isso, o planejamento de equipes passa a ser mais preciso em períodos de pico e de baixa.\nO modelo já está em uso para o planejamento do segundo semestre.",
    },
    {
        "category": "Operação",
        "published_at": date(2026, 4, 25),
        "author": "Coordenação de Operação",
        "title": "Atualização do procedimento de fechamento de turno",
        "summary": "O procedimento de fechamento de turno foi atualizado para incluir a conferência de pendências e registro de ocorrências.",
        "content": "O procedimento de fechamento de turno foi atualizado e agora inclui a conferência obrigatória de pendências.\nTodas as ocorrências do turno devem ser registradas no sistema antes do encerramento das atividades.\nA medida visa melhorar a continuidade entre turnos e a rastreabilidade das ocorrências.",
    },
    {
        "category": "Indicadores",
        "published_at": date(2026, 4, 22),
        "author": "BI & Indicadores",
        "title": "Novo painel de absenteísmo em tempo real",
        "summary": "Um novo painel permite acompanhar os índices de absenteísmo em tempo real, por área e período.",
        "content": "Foi disponibilizado um novo painel de absenteísmo, que permite o acompanhamento dos índices em tempo real.\nÉ possível filtrar os dados por área e período, facilitando a identificação de tendências e a tomada de ação.\nO painel está acessível na seção de Indicadores para os perfis autorizados.",
    },
    {
        "category": "Geral",
        "published_at": date(2026, 4, 18),
        "author": "Comunicação Interna",
        "title": "Inscrições abertas para o programa de mentoria",
        "summary": "O programa interno de mentoria está com inscrições abertas para mentores e mentorados. Participe e desenvolva-se.",
        "content": "O programa interno de mentoria está com inscrições abertas para colaboradores interessados em atuar como mentores ou mentorados.\nA iniciativa busca acelerar o desenvolvimento profissional e fortalecer a troca de conhecimento entre as equipes.\nAs inscrições podem ser feitas até o fim do mês pelo portal do RH.",
    },
]


class Command(BaseCommand):
    help = "Cria notícias de exemplo no container de Comunicação (se a tabela estiver vazia)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recria as notícias de exemplo mesmo que já existam registros.",
        )

    def handle(self, *args, **options):
        if News.objects.exists() and not options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    "Já existem notícias cadastradas. Use --force para recriar os exemplos."
                )
            )
            return

        if options["force"]:
            deleted, _ = News.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"{deleted} registro(s) removido(s)."))

        created = 0
        for item in SAMPLE_NEWS:
            News.objects.create(**item)
            created += 1

        self.stdout.write(self.style.SUCCESS(f"{created} notícia(s) de exemplo criada(s)."))
