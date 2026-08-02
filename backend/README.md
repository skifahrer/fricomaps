# FricoMaps backend (NestJS)

Minimálny NestJS skeleton – základ pre budúce API (užívatelia, obľúbené
miesta, vyhľadávanie, push notifikácie…). Mapové dlaždice a štýly backend
**neservíruje** – tie idú staticky z GitHub Pages (PMTiles + range requesty).

## Endpointy

| Metóda | Cesta | Popis |
|---|---|---|
| GET | `/api/health` | health check |
| GET | `/api/regions` | zoznam regiónov (zdroj: `workers/regions.json`) |
| GET | `/api/regions/:key` | detail regiónu (`slovensko`, `zilinsky`, …) |

## Spustenie

```bash
cd backend
npm install
npm run start:dev   # http://localhost:3000/api/health
```

Produkčný build: `npm run build && npm run start:prod`.
