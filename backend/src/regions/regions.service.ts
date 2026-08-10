import { Injectable, NotFoundException } from '@nestjs/common';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

export interface RegionDef {
  name: string;
  osm_name: string;
  admin_level: number;
  bbox: [number, number, number, number];
}

/**
 * Zdroj pravdy o regiónoch je workers/data/regions.json (ten istý súbor
 * používa pipeline). Backend ho servíruje mobilným klientom, aby appka
 * nemusela mať zoznam regiónov zabudovaný napevno.
 */
@Injectable()
export class RegionsService {
  private readonly regions: Record<string, RegionDef> = JSON.parse(
    readFileSync(join(__dirname, '..', '..', '..', 'workers', 'regions.json'), 'utf8'),
  );

  findAll(): Record<string, RegionDef> {
    return this.regions;
  }

  findOne(key: string): RegionDef {
    const region = this.regions[key];
    if (!region) throw new NotFoundException(`Región '${key}' neexistuje`);
    return region;
  }
}
