# AoE4 Squad Stats

Site de histórico e winrate das partidas que **jogamos juntos** em Age of Empires IV.
Puxa da API pública do [AoE4World](https://aoe4world.com/api), guarda em SQLite e publica
tabelas + gráficos.

## Como funciona

```
aoe4world API ──sync.py──> SQLite ──stats.py──> FastAPI /api ──> web/ (Chart.js)
```

- `sync.py` baixa o histórico **completo** de cada jogador (`GET /api/v0/players/{id}/games`,
  50 por página) e faz upsert por `game_id`. Rodadas seguintes são incrementais: param na
  primeira página que não traz nada novo.
- Uma partida entra nas estatísticas quando **≥ N jogadores monitorados estão no mesmo time**
  (padrão N=2). É isso que torna o winrate representativo do grupo.
- `stats.py` faz todas as agregações em SQL sobre uma CTE `base` (uma linha por partida elegível).
- `summary.py` baixa o **resumo detalhado** de cada partida elegível e é o que alimenta o
  Comparativo e a seção de unidades econômicas (detalhes abaixo).

## Jogadores monitorados

Editar `players.json`. Só precisa do `profile_id`:

```json
{ "profile_id": 24270406, "alias": "floiiD" }
```

Como achar o `profile_id` de alguém novo:

```bash
curl -s -A "aoe4-friends-stats/1.0" "https://aoe4world.com/api/v0/players/search?query=NOME"
```

Ou pela URL do perfil: `aoe4world.com/players/24270406-floiiD` → id `24270406`.

Requisito: a pessoa precisa estar com **Match History pública** no jogo, senão o histórico
não aparece na API.

Já configurados:

| Alias | profile_id | Nome no jogo |
|---|---|---|
| floiiD | 24270406 | floiiD, the slippery bastard |
| CORONEL767 | 23812852 | CORONEL767 |
| pedrxness | 24295731 | pedrxness |
| vitts | 23335941 | vitts de vitera |

## Partidas personalizadas (custom)

**A API pública não expõe partidas personalizadas.** O `leaderboard` aceito pela API cobre só
`rm_*` e `qm_*`; o AoE4World só mostra custom para quem loga com Steam na própria conta. Por isso
o filtro "Personalizadas" existe mas fica vazio até você alimentar manualmente:

```bash
python backend/sync.py --import-custom minhas_customs.json
```

Formato (use `game_id` negativo para não colidir com ids da API):

```json
[{
  "game_id": -1,
  "started_at": "2026-08-01T20:00:00Z",
  "duration": 1800,
  "map": "Dry Arabia",
  "kind": "custom_3v3",
  "teams": [
    [{"player": {"profile_id": 24270406, "result": "win",  "civilization": "french"}},
     {"player": {"profile_id": 23812852, "result": "win",  "civilization": "english"}}],
    [{"player": {"profile_id": 24295731, "result": "loss", "civilization": "mongols"}}]
  ]
}]
```

Essas partidas ficam com `source = 'manual'` e aparecem no preset `custom`.

## Rodar local

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux: source .venv/bin/activate
pip install -r requirements.txt
python backend/sync.py --full                       # primeira carga (alguns minutos)
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --app-dir backend
```

Abrir <http://127.0.0.1:8000>.

## API

| Rota | O que devolve |
|---|---|
| `GET /api/stats` | todos os agregados de uma vez (resumo, mapas, civs, formações, timeline, comparativo, economia) |
| `GET /api/comparison` | só o comparativo jogador × métrica + unidades econômicas (`mode=avg\|sum`) |
| `GET /api/games/{game_id}` | detalhe de uma partida: aldeões perdidos por jogador dos dois times + comparativo |
| `GET /api/games` | lista paginada de partidas com times e civs |
| `GET /api/facets` | jogadores, modos e temporadas disponíveis (para montar filtros) |
| `GET /api/health` | contagem de partidas e último sync |
| `GET /api/docs` | OpenAPI |

Parâmetros comuns: `preset` (`tg`, `tg_ranked`, `tg_qm`, `ffa`, `custom`, `all`),
`players` (ids separados por vírgula que precisam estar **juntos** no mesmo time),
`min_size`, `from`, `to`, `season`, `map`.

## Comparativo e unidades econômicas

A API pública `/api/v0` só entrega o placar da partida (quem jogou, civ, resultado, rating).
Pontuação, recursos gastos, abates e build order ficam em outro endpoint, o mesmo que o site do
AoE4World usa na página de cada jogo:

```
GET https://www.aoe4world.com/players/{profile_id}/games/{game_id}/summary?camelize=true
```

Ele responde JSON e funciona sem assinatura para quem está com o **Match History público**.
Partida antiga costuma não ter resumo (devolve 404) — esse status fica gravado para não
repetir a chamada.

```bash
python backend/sync.py --summaries                    # baixa o que falta
python backend/sync.py --summaries --summaries-limit 50
python backend/sync.py --no-summaries                 # sync normal sem os resumos
```

O sync normal já baixa até `summaries_per_run` resumos por rodada (padrão 150, configurável em
`players.json`). Só partidas elegíveis entram na fila.

O que sai do resumo:

| Tabela | Conteúdo |
|---|---|
| `game_summaries` | status do download por partida (`ok`, `missing`, `error`) |
| `player_summaries` | pontuação (total/militar/econômica/tecnológica/social), recursos gastos e coletados por tipo, abates, perdas, arrasados, construções, pesquisas, APM |
| `unit_stats` | produzidas e perdidas **por tipo de unidade**, com categoria (`eco`, `militar`, `cerco`, `religioso`, `explorador`) |

**Comparativo** é a matriz jogador × métrica, com barra em cada célula proporcional ao maior
valor da coluna (mesma leitura do "Comparison" do AoE4World). Alterna entre média por partida
e total do período.

**Unidades econômicas eliminadas** usa `unit_stats`. Uma ressalva importante: o resumo diz
quais unidades **cada jogador perdeu**, não quem deu o abate. Então:

- *eliminadas* = soma das perdas econômicas do time adversário nas partidas do recorte —
  é crédito do time, não dá para atribuir a um jogador;
- *perdidas* = individual, direto do resumo de cada um.

### Por que não dá para saber quem matou o aldeão

Cada jogador tem três contadores de abate no resumo, e todos os três foram conferidos contra
as perdas do time adversário partida a partida:

| Contador | O que é de fato |
|---|---|
| `elitekill` (a coluna "Kills" do AoE4World) | unidades **militares** inimigas mortas — bate exato com a soma das perdas militares do outro time |
| `ekills` | `elitekill` + prédios inimigos destruídos |
| `sqkill` | mesma coisa contada por esquadrão, não por unidade |

Aldeão morto não entra em nenhum deles. A única marca de um aldeão que caiu está no build
order da **vítima** (`destroyed`), que registra o segundo da perda. Por isso o site mostra
quantos aldeões cada jogador perdeu e quando, nunca quem matou. O replay `.rec` também não
ajuda: é log de comandos de input, não de dano.

### Tela "aldeões perdidos"

Cada linha de **Últimas partidas** tem o botão *aldeões perdidos*, que abre o detalhe da
partida: aldeões produzidos, perdidos, % perdido e sobreviventes de **cada jogador dos dois
times**, com o pior minuto e uma barra por minuto de jogo mostrando quando a economia caiu.
Dentro dele, o comparativo completo daquela partida.

A linha do tempo depende da coluna `unit_stats.lost_at` (JSON com o segundo de cada perda),
que só é preenchida por resumos baixados depois dessa mudança. Para repopular os antigos:

```bash
python backend/sync.py --summaries --redo-all --summaries-limit 999
```

## Deploy na VM free da Oracle

VM `VM.Standard.A1.Flex` (ARM) com Ubuntu serve de sobra — o banco tem alguns MB.
As units em `deploy/` assumem o checkout em **`/opt/agezada`** rodando como o usuário de
serviço **`aoe4`**. Mudou de lugar? Troque os caminhos nos três arquivos antes de copiar.

```bash
sudo apt update && sudo apt install -y python3-venv git caddy
sudo useradd -r -m -d /opt/agezada aoe4
sudo -u aoe4 git clone <repo> /opt/agezada
cd /opt/agezada
sudo -u aoe4 python3 -m venv .venv
sudo -u aoe4 .venv/bin/pip install -r requirements.txt
sudo -u aoe4 mkdir -p data
sudo -u aoe4 .venv/bin/python backend/sync.py --full          # primeira carga
sudo -u aoe4 .venv/bin/python backend/sync.py --summaries --summaries-limit 999

sudo cp deploy/agezada*.service deploy/agezada-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agezada.service agezada-sync.timer

sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # troque o domínio antes
sudo systemctl reload caddy
```

Deploys seguintes:

```bash
/opt/agezada/deploy/deploy.sh       # pull + deps + restart + health check
```

O restart é obrigatório: `git pull` não troca o código já carregado na memória do uvicorn.
O health check avisa se a resposta vier sem a chave `summaries` — sinal de que o processo
antigo continuou de pé, ou de que o pull foi feito em outro checkout que ninguém serve.

### Migrando de uma instalação antiga (`aoe4stats`)

Instalação anterior à renomeação vive em `/opt/aoe4stats` com a unit `aoe4stats.service`:

```bash
sudo systemctl disable --now aoe4stats.service aoe4stats-sync.timer
sudo rm -f /etc/systemd/system/aoe4stats*.service /etc/systemd/system/aoe4stats*.timer
sudo mv /opt/aoe4stats /opt/agezada

# O venv guarda o caminho antigo nos shebangs — recriar é mais simples que corrigir.
sudo -u aoe4 python3 -m venv --clear /opt/agezada/.venv
sudo -u aoe4 /opt/agezada/.venv/bin/pip install -r /opt/agezada/requirements.txt

cd /opt/agezada && sudo -u aoe4 git pull --ff-only
sudo cp deploy/agezada*.service deploy/agezada-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agezada.service agezada-sync.timer
curl -s localhost:8000/api/health
```

O banco (`data/aoe4.db`) vai junto no `mv`, então nada é ressincronizado do zero.

**Rede da Oracle — dois firewalls, não um.** Liberar 80/443 na Security List/NSG da VCN **e**
na instância (Ubuntu da Oracle vem com regras `REJECT` no iptables):

```bash
sudo iptables -I INPUT 6 -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Verificações úteis:

```bash
systemctl status agezada
journalctl -u agezada-sync -n 50
curl -s localhost:8000/api/health          # traz games, summaries e last_sync
systemctl list-timers agezada-sync.timer
```

## Boas práticas com a API

O AoE4World pede uso educado: `User-Agent` identificando a aplicação (configurável em
`players.json`), cache local e chamadas incrementais. `sync.py` faz os três, com pausa de
0,5 s entre requests e retry com backoff em 429/5xx. Não baixe o histórico completo em loop —
`--full` é para a primeira carga.
