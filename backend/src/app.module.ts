import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { RegionsController } from './regions/regions.controller';
import { RegionsService } from './regions/regions.service';

@Module({
  controllers: [AppController, RegionsController],
  providers: [RegionsService],
})
export class AppModule {}
